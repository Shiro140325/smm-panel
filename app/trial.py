"""Free trial order for new accounts: one order of TRIAL_QTY on the cheapest TikTok views service.

Replaces the old welcome credit, which people could spend on anything. The trial is free only
on the trial service at exactly the trial quantity, once per account and once per link, and
only for accounts created after it was introduced (users.trial_used_at is set for older ones).
"""
import time

from app.config import get_settings
from app.pricing import SERVICE_SELECT

TRIAL_QTY = 1000
_cache: tuple[float, int | None] = (0.0, None)


async def trial_service_id(db) -> int | None:
    """The cheapest orderable TikTok views service that takes TRIAL_QTY (re-picked every 10 minutes)."""
    global _cache
    if _cache[1] and time.monotonic() - _cache[0] < 600:   # "none found" is never cached
        return _cache[1]
    sid = await db.fetch_val(SERVICE_SELECT + """
        and s.platform = 'tiktok' and s.category = 'Views'
        and ps.min_qty <= :q and ps.max_qty >= :q and lower(coalesce(ps.type, '')) <> 'custom comments'
        order by ps.rate, s.id limit 1""", {"q": TRIAL_QTY})
    _cache = (time.monotonic(), sid)
    return sid


async def trial_for(db, user_id: int) -> dict:
    """What the dashboard needs: the trial service and quantity, and whether this account can still use it."""
    sid = await trial_service_id(db) if get_settings().trial_enabled else None
    used = await db.fetch_val("select trial_used_at is not null from users where id = :u", {"u": user_id})
    return {"service_id": sid, "quantity": TRIAL_QTY, "available": bool(sid) and not used}
