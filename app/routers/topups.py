import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.db import transaction
from app.payments import TOPUP_TTL_MINUTES, close_topup, create_checkout, expire_stale_topups, reconcile_topup
from app.security import current_user

router = APIRouter(prefix="/topups", tags=["topups"])


class TopupIn(BaseModel):
    amount_php: int


@router.post("")
async def create_topup(body: TopupIn, user: dict = Depends(current_user)):
    s = get_settings()
    if not s.topup_min_php <= body.amount_php <= s.topup_max_php:
        raise HTTPException(400, f"Amount must be between ₱{s.topup_min_php:,} and ₱{s.topup_max_php:,}")
    if not s.paymongo_secret_key:
        raise HTTPException(503, "Payments are not configured")

    try:
        topup_id, url = await create_checkout(
            user["id"], body.amount_php, "Wallet top-up",
            success_url=f"{s.frontend_origin}/dashboard/#funds?status=success&topup={{topup_id}}",
            cancel_url=f"{s.frontend_origin}/dashboard/#funds?status=cancel")
    except RuntimeError:
        raise HTTPException(502, "Couldn't start checkout. Try again.")
    return {"topup_id": topup_id, "checkout_url": url}


@router.post("/{topup_id}/check")
async def check_topup(topup_id: str, user: dict = Depends(current_user)):
    """Called when the customer returns from checkout: ask PayMongo directly instead of waiting for the webhook."""
    try:
        topup_id = str(uuid.UUID(topup_id))
    except ValueError:
        raise HTTPException(404, "Top-up not found")
    async with transaction() as db:
        t = await db.fetch_one(
            "select id, checkout_id, status from topups where id = CAST(:id AS uuid) and user_id = :u",
            {"id": topup_id, "u": user["id"]},
        )
    if not t:
        raise HTTPException(404, "Top-up not found")
    if t["status"] in ("pending", "canceled", "expired"):
        await reconcile_topup(t)
        async with transaction() as db:
            t = await db.fetch_one("select status from topups where id = CAST(:id AS uuid)", {"id": topup_id})
    return {"status": t["status"]}


@router.post("/{topup_id}/cancel")
async def cancel_topup(topup_id: str, user: dict = Depends(current_user)):
    """Customer gives up on a payment: close the PayMongo checkout. If it was already paid, it's credited instead."""
    try:
        topup_id = str(uuid.UUID(topup_id))
    except ValueError:
        raise HTTPException(404, "Top-up not found")
    async with transaction() as db:
        t = await db.fetch_one(
            "select id, checkout_id, status from topups where id = CAST(:id AS uuid) and user_id = :u",
            {"id": topup_id, "u": user["id"]},
        )
    if not t:
        raise HTTPException(404, "Top-up not found")
    if t["status"] != "pending":
        return {"status": t["status"]}
    return {"status": await close_topup(t, "canceled")}


@router.get("")
async def list_topups(user: dict = Depends(current_user)):
    await expire_stale_topups(user["id"])   # so the list is exact, not just at the next background sync
    async with transaction() as db:
        return await db.fetch_all(f"""
            select id, amount_php, method, status, checkout_url, created_at, credited_at,
                   created_at + interval '{TOPUP_TTL_MINUTES} minutes' as expires_at
              from topups where user_id = :u order by created_at desc limit 20
        """, {"u": user["id"]})
