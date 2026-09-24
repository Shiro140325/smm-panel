"""PayMongo top-up crediting, shared by the webhook, the on-return check and the background sync.

Crediting is idempotent: a top-up moves pending → credited once, and the ledger has a unique
(reason, ref) index, so the webhook and the fallback checks can race safely.
"""
import logging
import os
import uuid

import httpx

from app.config import get_settings
from app.db import transaction

log = logging.getLogger("payments")
PAYMONGO_API = os.environ.get("PAYMONGO_API_BASE", "https://api.paymongo.com/v1")


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
        row = await db.fetch_one("""
            with upd as (
              update topups set status = 'credited', credited_at = now(),
                                method = coalesce(CAST(:src AS text), method)
               where id = CAST(:id AS uuid) and status = 'pending' and amount_php = :amt
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


async def reconcile_pending_topups() -> int:
    """Background safety net for missed webhooks: recent pending top-ups with a checkout session."""
    async with transaction() as db:
        rows = await db.fetch_all("""
            select id, checkout_id from topups
             where status = 'pending' and checkout_id is not null
               and created_at > now() - interval '2 days'
             order by created_at desc limit 50
        """)
    n = 0
    for t in rows:
        try:
            n += await reconcile_topup(t)
        except Exception:
            log.exception("reconcile topup %s failed", t["id"])
    return n
