import hashlib
import hmac
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.db import transaction

log = logging.getLogger("webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_paymongo_signature(raw: bytes, header: str, secret: str) -> bool:
    """Header: 't=<unix>,te=<test sig>,li=<live sig>'. Sig = HMAC-SHA256(secret, f'{t}.{body}')."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    except ValueError:
        return False
    t = parts.get("t")
    sig = parts.get("li") or parts.get("te")
    if not t or not sig:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


@router.post("/paymongo")
async def paymongo_webhook(request: Request):
    raw = await request.body()
    secret = get_settings().paymongo_webhook_secret
    if not secret or not verify_paymongo_signature(raw, request.headers.get("Paymongo-Signature", ""), secret):
        raise HTTPException(401, "Bad signature")

    event = json.loads(raw)["data"]["attributes"]
    if event.get("type") != "checkout_session.payment.paid":
        return {"ok": True}

    sess = event["data"]["attributes"]
    topup_id = (sess.get("metadata") or {}).get("topup_id") or sess.get("reference_number")
    payments = sess.get("payments") or []
    try:
        topup_id = str(uuid.UUID(str(topup_id)))
    except ValueError:
        topup_id = None
    if not topup_id or not payments:
        log.warning("paid event without valid topup id/payments: %s", event["data"].get("id"))
        return {"ok": True}
    paid_php = sum(int(p["attributes"]["amount"]) for p in payments) // 100

    async with transaction() as db:
        credited = await db.fetch_one("""
            with upd as (
              update topups set status = 'credited', credited_at = now()
               where id = CAST(:id AS uuid) and status = 'pending' and amount_php = :amt
              returning id, user_id, amount_php
            )
            insert into ledger (user_id, delta, reason, ref)
            select user_id, amount_php, 'topup', id::text from upd
            on conflict do nothing
            returning user_id, delta
        """, {"id": topup_id, "amt": paid_php})
    if not credited:
        log.info("topup %s not credited (already credited, unknown, or amount mismatch %s)", topup_id, paid_php)
    return {"ok": True}
