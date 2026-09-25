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


async def _build(key: tuple, db: DB | None = None) -> tuple[float, bytes, bytes, str]:
    if db is None:
        async with transaction() as own:
            rows = await _rows(own, *key)
    else:
        rows = await _rows(db, *key)
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
    for key in ((None, False), (None, True)):
        await _build(key)


@router.get("")
async def list_services(request: Request, platform: str | None = None, featured: bool = False,
                        db: DB = Depends(get_db)):
    key = (platform, featured)
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


@router.get("/count")
async def count_services(db: DB = Depends(get_db)):
    """How many services customers can order (the landing page links to the full list with this)."""
    hit = _cache.get(("count",))
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    out = {"total": await db.fetch_val(f"select count(*) from ({SERVICE_SELECT}) x")}
    _cache[("count",)] = (time.monotonic(), out)
    return out
