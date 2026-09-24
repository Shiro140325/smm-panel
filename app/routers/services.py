from fastapi import APIRouter, Depends

from app.db import DB, get_db
from app.pricing import SERVICE_SELECT, price_per_1k_php

router = APIRouter(prefix="/services", tags=["services"])


@router.get("")
async def list_services(platform: str | None = None, db: DB = Depends(get_db)):
    sql = SERVICE_SELECT + (" and s.platform = :pf" if platform else "") + " order by s.platform, s.sort, s.id"
    rows = await db.fetch_all(sql, {"pf": platform} if platform else {})
    return [
        {
            "id": r["id"],
            "platform": r["platform"],
            "name": r["name"],
            "tier": r["tier"],
            "description": r["description"],
            "start_time": r["start_time"],
            "speed": r["speed"],
            "drop_risk": r["drop_risk"],
            "refill_days": r["refill_days"],
            # frontend: show a comments textarea (one per line) instead of a quantity field
            "custom_comments": (r["type"] or "").strip().lower() == "custom comments",
            "min": r["min_qty"],
            "max": r["max_qty"],
            "price_per_1k_php": price_per_1k_php(r["rate"], r["currency"], r["markup_pct"]),
        }
        for r in rows
    ]
