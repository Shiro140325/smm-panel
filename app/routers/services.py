import asyncio
import gzip
import hashlib
import json
import logging
import os
import time
import unicodedata

from fastapi import APIRouter, Depends, Request, Response

from app.catalog import NON_DROP, NON_DROP_DAYS
from app.db import DB, get_db, transaction
from app.pricing import SERVICE_SELECT, price_per_1k_php

router = APIRouter(prefix="/services", tags=["services"])
log = logging.getLogger("services")

# The full catalog is thousands of rows (~2 MB of JSON). Encoding and compressing it took seconds,
# so the finished response is cached: JSON bytes, their gzip, and an ETag, per query shape.
# After _CACHE_TTL a request still gets the cached copy while a fresh one is built in the
# background; only a missing (or, with TTL 0 in tests, any) cache entry is built inline.
_CACHE_TTL = float(os.environ.get("SERVICES_CACHE_SECONDS", "60"))
_cache: dict[tuple, tuple] = {}
_built: dict[tuple, tuple[float, bytes, bytes, str]] = {}
_refreshing: set[tuple] = set()


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
        "price_per_1k_php": price_per_1k_php(r["rate"], r["currency"], r["markup_pct"]),
    }


async def _rows(db: DB, platform: str | None, featured: bool) -> list[dict]:
    sql = SERVICE_SELECT
    params = {}
    if platform:
        sql += " and s.platform = :pf"
        params["pf"] = platform
    if featured:
        sql += " and not s.auto"
    rows = []
    for r in await db.fetch_all(sql, params):
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
    entry = (time.monotonic(), body, gzip.compress(body, 9), '"' + hashlib.md5(body).hexdigest() + '"')
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
                        db: DB = Depends(get_db)):
    return await _cached(request, (platform, featured), db)


@router.get("/top")
async def top_services(request: Request, db: DB = Depends(get_db)):
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
async def service_timing(db: DB = Depends(get_db)):
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
async def count_services(db: DB = Depends(get_db)):
    """How many services customers can order (the landing page links to the full list with this)."""
    hit = _cache.get(("count",))
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    out = {"total": await db.fetch_val(f"select count(*) from ({SERVICE_SELECT}) x")}
    _cache[("count",)] = (time.monotonic(), out)
    return out
