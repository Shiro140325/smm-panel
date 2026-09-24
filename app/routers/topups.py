import uuid
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.db import transaction
from app.security import current_user

router = APIRouter(prefix="/topups", tags=["topups"])

PAYMONGO_API = "https://api.paymongo.com/v1"


class TopupIn(BaseModel):
    amount_php: int
    method: Literal["gcash", "paymaya"] = "gcash"


@router.post("")
async def create_topup(body: TopupIn, user: dict = Depends(current_user)):
    s = get_settings()
    if not s.topup_min_php <= body.amount_php <= s.topup_max_php:
        raise HTTPException(400, f"Amount must be between ₱{s.topup_min_php:,} and ₱{s.topup_max_php:,}")
    if not s.paymongo_secret_key:
        raise HTTPException(503, "Payments are not configured")

    topup_id = str(uuid.uuid4())
    async with transaction() as db:
        await db.execute("""
            insert into topups (id, user_id, amount_php, method) values (:id, :u, :a, :m)
        """, {"id": topup_id, "u": user["id"], "a": body.amount_php, "m": body.method})

    payload = {"data": {"attributes": {
        "line_items": [{"name": "Wallet top-up", "amount": body.amount_php * 100,
                        "currency": "PHP", "quantity": 1}],
        "payment_method_types": [body.method],     # e-wallets only: cards invite chargebacks
        "reference_number": topup_id,
        "description": f"Wallet top-up ₱{body.amount_php:,}",
        "metadata": {"topup_id": topup_id},
        "success_url": f"{s.frontend_origin}/funds?status=success&topup={topup_id}",
        "cancel_url": f"{s.frontend_origin}/funds?status=cancel",
    }}}
    async with httpx.AsyncClient(auth=(s.paymongo_secret_key, ""), timeout=30) as c:
        r = await c.post(f"{PAYMONGO_API}/checkout_sessions", json=payload)
    if r.status_code >= 400:
        raise HTTPException(502, "Couldn't start checkout. Try again.")
    sess = r.json()["data"]

    async with transaction() as db:
        await db.execute("""
            update topups set checkout_id = :cid, checkout_url = :url where id = :id
        """, {"cid": sess["id"], "url": sess["attributes"]["checkout_url"], "id": topup_id})
    return {"topup_id": topup_id, "checkout_url": sess["attributes"]["checkout_url"]}


@router.get("")
async def list_topups(user: dict = Depends(current_user)):
    async with transaction() as db:
        return await db.fetch_all("""
            select id, amount_php, method, status, checkout_url, created_at, credited_at
              from topups where user_id = :u order by created_at desc limit 20
        """, {"u": user["id"]})
