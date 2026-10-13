import asyncio
import gzip
import hashlib
import json
import logging
import os
import time
import unicodedata
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response

from app.catalog import NON_DROP, NON_DROP_DAYS
from app.db import DB, get_db, transaction
from app.pricing import SERVICE_COLUMNS, SERVICE_FROM, SERVICE_ORDERABLE, SERVICE_SELECT, price_per_1k_php

router = APIRouter(prefix="/services", tags=["services"])
log = logging.getLogger("services")

# The full catalog is thousands of rows (~2 MB of JSON). Encoding and compressing it took seconds,
# so the finished response is cached: JSON bytes, their gzip, and an ETag, per query shape.
# After _CACHE_TTL a request still gets the cached copy while a fresh one is built in the
# background; only a missing (or, with TTL 0 in tests, any) cache entry is built inline.
# The rows behind every list are kept in memory with a fingerprint of the catalog (a 32-byte md5
# the database computes). When the fingerprint changes, only the rows changed since the last
# download are fetched (changed_at, kept by triggers; see db/schema.sql) and merged in; the whole
# catalog is downloaded only at first, every CATALOG_FULL_EVERY, or if a merge doesn't add up.
# Pulling the full catalog on every rebuild was most of the database's egress.
_CACHE_TTL = float(os.environ.get("SERVICES_CACHE_SECONDS", "60"))
_cache: dict[tuple, tuple] = {}
_built: dict[tuple, tuple[float, bytes, bytes, str]] = {}
_refreshing: set[tuple] = set()
_catalog: dict | None = None    # fingerprint, as_of (database clock), full_at, rows (by id), list
_catalog_checked = 0.0          # when the fingerprint was last compared
CATALOG_RECHECK = 5             # seconds: lists rebuilt together (warm_cache) share one fingerprint check
CATALOG_FULL_EVERY = 6 * 3600   # seconds: a full download now and then, in case a change slipped past
CATALOG_OVERLAP = timedelta(minutes=2)   # re-read recent changes too: a slow transaction commits late
CATALOG_HEAD = f"""
    select md5(coalesce(string_agg(x::text, ',' order by x.id), '')) as fingerprint, count(*) as n,
           statement_timestamp() as as_of
      from ({SERVICE_SELECT}) x"""
CATALOG_CHANGES = f"""
    select {SERVICE_COLUMNS}, ({SERVICE_ORDERABLE}) as orderable
    {SERVICE_FROM}
     where greatest(s.changed_at, ps.changed_at, p.changed_at) > :since"""


def _row(r) -> dict:
    pname = unicodedata.normalize("NFKC", r["provider_name"] or "")   # provider names use fancy fonts
    return {
        "id": r["id"],
        "platform": r["platform"],
        "category": r["category"],
        "featured": not r["auto"],
        "name": r["name"],
        "tier": r["tier"],
        "description": r["description"],
        "start_time": r["start_time"],
        "speed": r["speed"],
        "drop_risk": r["drop_risk"],
        "refill_days": r["refill_days"],
        "non_drop": bool(NON_DROP.search(pname)),
        "non_drop_days": int(m.group(1)) if (m := NON_DROP_DAYS.search(pname)) else None,   # None = no limit stated
        # frontend: show a comments textarea (one per line) instead of a quantity field
        "custom_comments": (r["type"] or "").strip().lower() == "custom comments",
        "min": r["min_qty"],
        "max": r["max_qty"],
        "price_per_1k_php": price_per_1k_php(r["rate"], r["currency"], r["markup_pct"], r["price_php"]),
    }


async def _catalog_rows(db: DB) -> list[dict]:
    """Every orderable service (SERVICE_SELECT): only what changed is downloaded (see above)."""
    global _catalog, _catalog_checked
    if _catalog is not None and _CACHE_TTL > 0 and time.monotonic() - _catalog_checked < CATALOG_RECHECK:
        return _catalog["list"]
    head = await db.fetch_one(CATALOG_HEAD)
    if _catalog is None or _catalog["fingerprint"] != head["fingerprint"]:
        rows = None
        if _catalog is not None and time.monotonic() - _catalog["full_at"] < CATALOG_FULL_EVERY:
            rows = await _merge_changes(db, _catalog)
            if rows is not None and len(rows) != head["n"]:   # a row deleted outright: start over
                rows = None
        full_at = _catalog["full_at"] if rows is not None else time.monotonic()
        if rows is None:
            rows = {r["id"]: r for r in await db.fetch_all(SERVICE_SELECT)}
        _catalog = {"fingerprint": head["fingerprint"], "as_of": head["as_of"], "full_at": full_at,
                    "rows": rows, "list": sorted(rows.values(), key=lambda r: r["id"])}
    else:
        _catalog["as_of"] = head["as_of"]   # still current: the next changes are read from here
    _catalog_checked = time.monotonic()
    return _catalog["list"]


async def _merge_changes(db: DB, catalog: dict) -> dict[int, dict] | None:
    """The cached rows with the changes since the last download applied, or None if they can't be read."""
    try:
        async with db.conn.begin_nested():   # a failure (e.g. changed_at not added yet) leaves the request usable
            changed = await db.fetch_all(CATALOG_CHANGES, {"since": catalog["as_of"] - CATALOG_OVERLAP})
    except Exception:
        log.exception("reading catalog changes failed; downloading all of it")
        return None
    rows = dict(catalog["rows"])
    for r in changed:
        if r.pop("orderable"):
            rows[r["id"]] = r
        else:
            rows.pop(r["id"], None)   # hidden, switched off or no longer listed
    return rows


def forget_catalog():
    """Drop every cached list, so the next request rebuilds from the database (after an admin edit)."""
    global _catalog
    _catalog = None
    _built.clear()
    _cache.clear()


async def _rows(db: DB, platform: str | None, featured: bool) -> list[dict]:
    rows = []
    for r in await _catalog_rows(db):
        if (platform and r["platform"] != platform) or (featured and r["auto"]):
            continue
        item = _row(r)
        # hand-picked services first (curated order), then the rest cheapest-first per category
        rank = (r["platform"], 0, r["sort"], r["id"]) if not r["auto"] else \
            (r["platform"], 1, item["category"] or "", item["price_per_1k_php"], r["id"])
        rows.append((rank, item))
    return [item for _, item in sorted(rows, key=lambda x: x[0])]


TOP_KEY = ("__top__", 65)   # home page table: per platform, the 5 cheapest of each category, max 65
TOP_PER_CATEGORY = 5
# followers first, then what customers look for most; unknown categories (site names under "other") after
CATEGORY_ORDER = ["Followers", "Subscribers", "Members", "Likes", "Views", "Comments", "Reactions", "Shares",
                  "Saves", "Plays", "Stories", "Live stream", "Comment likes", "Other"]


def _top(rows: list[dict], per_platform: int) -> list[dict]:
    rank = lambda c: (CATEGORY_ORDER.index(c), "") if c in CATEGORY_ORDER else (len(CATEGORY_ORDER), c)
    out, groups = [], {}
    for r in rows:
        groups.setdefault(r["platform"], {}).setdefault(r["category"] or "Other", []).append(r)
    for cats in groups.values():
        picked = []
        for cat in sorted(cats, key=rank):
            picked += sorted(cats[cat], key=lambda r: (r["price_per_1k_php"], r["id"]))[:TOP_PER_CATEGORY]
        out += picked[:per_platform]
    return out


async def _produce(db: DB, key: tuple) -> list[dict]:
    if key[0] == TOP_KEY[0]:
        return _top(await _rows(db, None, False), key[1])
    return await _rows(db, *key)


async def _build(key: tuple, db: DB | None = None) -> tuple[float, bytes, bytes, str]:
    if db is None:
        async with transaction() as own:
            rows = await _produce(own, key)
    else:
        rows = await _produce(db, key)
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    etag = '"' + hashlib.md5(body).hexdigest() + '"'
    old = _built.get(key)
    if old and old[3] == etag:   # unchanged: keep the gzip instead of compressing it again
        entry = (time.monotonic(), old[1], old[2], etag)
    else:
        entry = (time.monotonic(), body, gzip.compress(body, 9), etag)
    _built[key] = entry
    return entry


async def _refresh(key: tuple):
    try:
        await _build(key)
    except Exception:
        log.exception("services cache refresh failed for %s", key)
    finally:
        _refreshing.discard(key)


async def warm_cache():
    """Build the lists the site asks for, so no visitor waits for the first build (called by the sync)."""
    for key in ((None, False), (None, True), TOP_KEY):
        await _build(key)


@router.get("")
async def list_services(request: Request, platform: str | None = None, featured: bool = False,
                        db: DB = Depends(get_db, scope="function")):
    return await _cached(request, (platform, featured), db)


@router.get("/top")
async def top_services(request: Request, db: DB = Depends(get_db, scope="function")):
    """The home page price table: per platform, the 5 cheapest of each category, followers first (max 65)."""
    return await _cached(request, TOP_KEY, db)


async def _cached(request: Request, key: tuple, db: DB) -> Response:
    entry = _built.get(key)
    if entry is None or _CACHE_TTL <= 0:
        entry = await _build(key, db)
    elif time.monotonic() - entry[0] >= _CACHE_TTL and key not in _refreshing:
        _refreshing.add(key)
        asyncio.create_task(_refresh(key))   # serve this copy; the next request gets the fresh one
    _, body, gz, etag = entry
    headers = {"ETag": etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)   # browser's copy is current: nothing to send
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gz, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(body, media_type="application/json", headers=headers)


TIMING_ORDERS = 15      # average over exactly this many of a service's latest completed orders
TIMING_TTL = 300


@router.get("/timing")
async def service_timing(db: DB = Depends(get_db, scope="function")):
    """Per service, from its latest completed orders (all customers): how many we have (up to 15),
    their average completion time once there are 15, and how long the most recent one took.
    Completion time = order placed → provider reported it completed."""
    hit = _cache.get(("timing",))
    if hit and time.monotonic() - hit[0] < TIMING_TTL:
        return hit[1]
    rows = await db.fetch_all(f"""
        with r as (
          select service_id, completed_at, extract(epoch from (completed_at - created_at)) as sec,
                 row_number() over (partition by service_id order by completed_at desc) as rn
            from orders
           where status = 'completed' and completed_at is not null and completed_at >= created_at
        )
        select service_id, count(*) as n, avg(sec)::int as avg_sec,
               max(case when rn = 1 then sec end)::int as last_sec, max(completed_at) as last_at
          from r where rn <= {TIMING_ORDERS}
         group by service_id
    """)
    out = {str(r["service_id"]): {"n": r["n"], "avg_seconds": r["avg_sec"] if r["n"] >= TIMING_ORDERS else None,
                                  "last_seconds": r["last_sec"], "last_completed_at": r["last_at"]} for r in rows}
    _cache[("timing",)] = (time.monotonic(), out)
    return out


@router.get("/count")
async def count_services(db: DB = Depends(get_db, scope="function")):
    """How many services customers can order (the landing page links to the full list with this)."""
    hit = _cache.get(("count",))
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    out = {"total": len(await _catalog_rows(db))}
    _cache[("count",)] = (time.monotonic(), out)
    return out
