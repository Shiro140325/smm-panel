"""Free trial order for new accounts: one order of up to TRIAL_QTY on TRIAL_SERVICE_ID.

One per account, per link, per device (a random id the browser keeps in storage and a cookie)
and per network (IP address, 30 days).

Replaces the old welcome credit, which people could spend on anything. The trial is free only
on the trial service up to the trial quantity, once per account and once per link, and
only for accounts created after it was introduced (users.trial_used_at is set for older ones).
"""
import re
import time

from fastapi import Request

from app.config import get_settings
from app.pricing import SERVICE_SELECT

TRIAL_SERVICE_ID = 237   # Facebook Post Reaction (Care)
TRIAL_QTY = 100          # the most a trial order can be; any amount from the service minimum up to this is free
_cache: tuple[float, int | None] = (0.0, None)


async def trial_service_id(db) -> int | None:
    """TRIAL_SERVICE_ID while it's orderable and takes TRIAL_QTY (checked every 10 minutes)."""
    global _cache
    if _cache[1] and time.monotonic() - _cache[0] < 600:   # "not orderable" is never cached
        return _cache[1]
    sid = await db.fetch_val(SERVICE_SELECT + """
        and s.id = :id and ps.min_qty <= :q and ps.max_qty >= :q
        and lower(coalesce(ps.type, '')) <> 'custom comments' limit 1""", {"id": TRIAL_SERVICE_ID, "q": TRIAL_QTY})
    _cache = (time.monotonic(), sid)
    return sid


TRIAL_IP_DAYS = 30


def device_of(request: Request) -> str | None:
    d = request.headers.get("x-device") or request.cookies.get("dev") or ""
    return d if re.fullmatch(r"[a-f0-9]{32}", d) else None


async def trial_for(db, user_id: int, device: str | None = None, ip: str | None = None) -> dict:
    """What the dashboard needs: the trial service and quantity, and whether this account, on this
    device and network, can still use it."""
    sid = await trial_service_id(db) if get_settings().trial_enabled else None
    used = await db.fetch_val("select trial_used_at is not null from users where id = :u", {"u": user_id})
    taken = bool(await db.fetch_val(f"""
        select 1 from users
         where id <> :u and trial_used_at is not null
           and ((CAST(:d AS text) is not null and trial_device = :d)
                or (CAST(:ip AS text) is not null and trial_ip = :ip and trial_used_at > now() - interval '{TRIAL_IP_DAYS} days'))
         limit 1""", {"u": user_id, "d": device, "ip": ip}))
    return {"service_id": sid, "quantity": TRIAL_QTY, "available": bool(sid) and not used and not taken}
