import os
import time
import unicodedata

from fastapi import APIRouter, Depends

from app.catalog import NON_DROP, NON_DROP_DAYS
from app.db import DB, get_db
from app.pricing import SERVICE_SELECT, price_per_1k_php

router = APIRouter(prefix="/services", tags=["services"])

# The full catalog is thousands of rows; cache the priced list briefly per query shape.
_CACHE_TTL = float(os.environ.get("SERVICES_CACHE_SECONDS", "60"))
_cache: dict[tuple, tuple[float, list]] = {}


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


@router.get("")
async def list_services(platform: str | None = None, featured: bool = False, db: DB = Depends(get_db)):
    key = (platform, featured)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
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
    rows = [item for _, item in sorted(rows, key=lambda x: x[0])]
    _cache[key] = (time.monotonic(), rows)
    return rows
