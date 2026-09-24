import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.payments import _paid, credit_topup

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
    header = request.headers.get("Paymongo-Signature", "")
    if not secret or not verify_paymongo_signature(raw, header, secret):
        log.warning("paymongo webhook rejected: %s", "no PAYMONGO_WEBHOOK_SECRET set" if not secret
                    else "missing signature header" if not header else "signature mismatch (wrong webhook secret?)")
        raise HTTPException(401, "Bad signature")

    event = json.loads(raw)["data"]["attributes"]
    log.info("paymongo webhook: %s", event.get("type"))
    if event.get("type") != "checkout_session.payment.paid":
        return {"ok": True}

    sess = event["data"]["attributes"]
    topup_id = (sess.get("metadata") or {}).get("topup_id") or sess.get("reference_number")
    paid_php, source = _paid(sess.get("payments"))
    if not topup_id or paid_php <= 0:
        log.warning("paid event without topup id/payments: %s", event["data"].get("id"))
        return {"ok": True}
    if not await credit_topup(topup_id, paid_php, source):
        log.info("topup %s not credited (already credited, unknown, or amount mismatch ₱%s)", topup_id, paid_php)
    return {"ok": True}
