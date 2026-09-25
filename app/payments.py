"""PayMongo top-up crediting, shared by the webhook, the on-return check and the background sync.

Crediting is idempotent: a top-up moves to credited once, and the ledger has a unique
(reason, ref) index, so the webhook and the fallback checks can race safely.

Unpaid top-ups are closed after TOPUP_TTL_MINUTES (or when the customer cancels): the PayMongo
checkout is expired so it can't be paid, and the row becomes 'canceled' / 'expired'. Money that
still arrives for a closed top-up is credited anyway, because it was really received.
"""
import logging
import os
import uuid

import httpx

from app.config import get_settings
from app.db import transaction

log = logging.getLogger("payments")
PAYMONGO_API = os.environ.get("PAYMONGO_API_BASE", "https://api.paymongo.com/v1")
TOPUP_TTL_MINUTES = 10
CREDITABLE = "('pending', 'canceled', 'expired')"   # a real payment is credited even after we closed it


def _paid(payments: list) -> tuple[int, str | None]:
    """Sum of paid payments in whole pesos, and the first payment's method (gcash, paymaya, ...)."""
    total, source = 0, None
    for p in payments or []:
        a = p.get("attributes") or {}
        if a.get("status", "paid") != "paid":
            continue
        total += int(a.get("amount") or 0)
        source = source or (a.get("source") or {}).get("type")
    return total // 100, source


async def credit_topup(topup_id: str, paid_php: int, source: str | None) -> bool:
    """Credit a pending top-up if the paid amount matches. Returns True if this call credited it."""
    try:
        topup_id = str(uuid.UUID(str(topup_id)))
    except ValueError:
        return False
    async with transaction() as db:
        row = await db.fetch_one(f"""
            with upd as (
              update topups set status = 'credited', credited_at = now(),
                                method = coalesce(CAST(:src AS text), method)
               where id = CAST(:id AS uuid) and status in {CREDITABLE} and amount_php = :amt
              returning id, user_id, amount_php
            )
            insert into ledger (user_id, delta, reason, ref)
            select user_id, amount_php, 'topup', id::text from upd
            on conflict do nothing
            returning user_id
        """, {"id": topup_id, "amt": paid_php, "src": str(source)[:20] if source else None})
    if row:
        log.info("topup %s credited ₱%s (%s)", topup_id, paid_php, source)
    return bool(row)


async def fetch_checkout(checkout_id: str) -> dict | None:
    key = get_settings().paymongo_secret_key
    if not key or not checkout_id:
        return None
    async with httpx.AsyncClient(auth=(key, ""), timeout=20) as c:
        r = await c.get(f"{PAYMONGO_API}/checkout_sessions/{checkout_id}")
    if r.status_code >= 400:
        log.warning("checkout %s lookup failed: %s %s", checkout_id, r.status_code, r.text[:200])
        return None
    return (r.json().get("data") or {}).get("attributes") or {}


async def reconcile_topup(topup: dict) -> bool:
    """Ask PayMongo whether this top-up's checkout was paid; credit it if so."""
    sess = await fetch_checkout(topup.get("checkout_id"))
    if not sess:
        return False
    paid_php, source = _paid(sess.get("payments"))
    if paid_php <= 0:
        return False
    return await credit_topup(str(topup["id"]), paid_php, source)


async def expire_checkout(checkout_id: str | None) -> bool:
    """Expire a PayMongo checkout session so it can no longer be paid."""
    key = get_settings().paymongo_secret_key
    if not key or not checkout_id:
        return False
    try:
        async with httpx.AsyncClient(auth=(key, ""), timeout=20) as c:
            r = await c.post(f"{PAYMONGO_API}/checkout_sessions/{checkout_id}/expire")
    except httpx.HTTPError:
        log.warning("checkout %s expire failed (network)", checkout_id)
        return False
    if r.status_code >= 400:
        log.warning("checkout %s expire failed: %s %s", checkout_id, r.status_code, r.text[:200])
        return False
    return True


async def close_topup(topup: dict, status: str) -> str:
    """Cancel or expire a pending top-up unless it was paid. Returns its final status."""
    if await reconcile_topup(topup):
        return "credited"
    await expire_checkout(topup.get("checkout_id"))
    if await reconcile_topup(topup):   # paid in the moment before the checkout closed
        return "credited"
    async with transaction() as db:
        row = await db.fetch_one("""
            update topups set status = :st where id = CAST(:id AS uuid) and status = 'pending' returning status
        """, {"st": status, "id": str(topup["id"])})
        if not row:   # something else (a webhook) already moved it on
            row = await db.fetch_one("select status from topups where id = CAST(:id AS uuid)", {"id": str(topup["id"])})
    return row["status"] if row else status


async def expire_stale_topups(user_id: int | None = None) -> int:
    """Close pending top-ups older than TOPUP_TTL_MINUTES (all users, or one)."""
    async with transaction() as db:
        rows = await db.fetch_all(f"""
            select id, checkout_id from topups
             where status = 'pending' and created_at < now() - interval '{TOPUP_TTL_MINUTES} minutes'
               {"and user_id = :u" if user_id is not None else ""}
             order by created_at limit 50
        """, {"u": user_id} if user_id is not None else {})
    n = 0
    for t in rows:
        try:
            n += await close_topup(t, "expired") == "expired"
        except Exception:
            log.exception("expire topup %s failed", t["id"])
    return n


async def reconcile_pending_topups() -> int:
    """Background safety net for missed webhooks: recent open (or just-closed) top-ups with a checkout."""
    async with transaction() as db:
        rows = await db.fetch_all(f"""
            select id, checkout_id from topups
             where status in {CREDITABLE} and checkout_id is not null
               and created_at > now() - interval '1 day'
             order by created_at desc limit 50
        """)
    n = 0
    for t in rows:
        try:
            n += await reconcile_topup(t)
        except Exception:
            log.exception("reconcile topup %s failed", t["id"])
    return n
