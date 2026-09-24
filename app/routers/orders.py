import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.db import transaction
from app.pricing import SERVICE_SELECT, order_price_php, price_per_1k_php
from app.providers.smm_client import ProviderError, SMMClient
from app.security import balance_of, current_user
from app.workers.sync import sync_user_orders

log = logging.getLogger("orders")
router = APIRouter(prefix="/orders", tags=["orders"])

OPEN_STATUSES = ("creating", "pending", "in_progress")


CUSTOM_COMMENTS = "custom comments"


class OrderIn(BaseModel):
    service_id: int
    link: str = Field(max_length=500)
    quantity: int = Field(gt=0)
    comments: str | None = Field(default=None, max_length=200_000)  # one per line, Custom Comments services only

    @field_validator("link")
    @classmethod
    def _link(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("https://", "http://")):
            raise ValueError("Link must start with https://")
        return v


@router.post("")
async def create_order(body: OrderIn, user: dict = Depends(current_user)):
    # 1) validate, price, debit balance, record order — one transaction, user row locked
    async with transaction() as db:
        svc = await db.fetch_one(SERVICE_SELECT + " and s.id = :id", {"id": body.service_id})
        if not svc:
            raise HTTPException(404, "Service not found")

        quantity, comments = body.quantity, None
        if (svc["type"] or "").strip().lower() == CUSTOM_COMMENTS:
            lines = [ln.strip() for ln in (body.comments or "").splitlines() if ln.strip()]
            if not lines:
                raise HTTPException(400, "Enter at least one comment, one per line")
            quantity, comments = len(lines), "\n".join(lines)   # quantity = number of comments

        if not svc["min_qty"] <= quantity <= svc["max_qty"]:
            raise HTTPException(400, f"Quantity must be between {svc['min_qty']} and {svc['max_qty']}")

        per_1k = price_per_1k_php(svc["rate"], svc["currency"], svc["markup_pct"])
        price = order_price_php(per_1k, quantity)

        await db.execute("select id from users where id = :u for update", {"u": user["id"]})
        if await balance_of(db, user["id"]) < price:
            raise HTTPException(402, "Not enough balance")

        order = await db.fetch_one("""
            insert into orders (user_id, service_id, provider_id, link, quantity, price_php, comments)
            values (:u, :s, :p, :l, :q, :price, :c) returning id
        """, {"u": user["id"], "s": svc["id"], "p": svc["provider_id"], "l": body.link,
              "q": quantity, "price": price, "c": comments})
        await db.execute(
            "insert into ledger (user_id, delta, reason, ref) values (:u, :d, 'order', :r)",
            {"u": user["id"], "d": -price, "r": str(order["id"])},
        )
        provider = await db.fetch_one("select * from providers where id = :p", {"p": svc["provider_id"]})

    # 2) place upstream — outside the transaction
    try:
        client = SMMClient.for_provider(provider)
        extra = {"comments": comments} if comments else {}
        provider_order_id = await client.add(svc["provider_service_id"], body.link, quantity, **extra)
    except ProviderError as e:
        # provider rejected it: mark failed and refund in full
        async with transaction() as db:
            await _fail_and_refund(db, order["id"], user["id"], price)
        raise HTTPException(400, f"Order rejected by provider: {e}")
    except Exception:
        # network error/timeout: we can't know whether it was placed — never auto-refund
        log.exception("provider add failed for order %s", order["id"])
        async with transaction() as db:
            await db.execute(
                "update orders set status = 'needs_review', updated_at = now() where id = :id",
                {"id": order["id"]},
            )
        raise HTTPException(502, "Couldn't confirm the order with our provider. Support will check it.")

    async with transaction() as db:
        await db.execute("""
            update orders set provider_order_id = :po, status = 'pending', updated_at = now()
            where id = :id
        """, {"po": provider_order_id, "id": order["id"]})

    return {"id": order["id"], "status": "pending", "quantity": quantity, "charge_php": price}


async def _fail_and_refund(db, order_id: int, user_id: int, price: float):
    await db.execute(
        "update orders set status = 'failed', updated_at = now() where id = :id", {"id": order_id}
    )
    await db.execute("""
        insert into ledger (user_id, delta, reason, ref) values (:u, :d, 'refund', :r)
        on conflict do nothing
    """, {"u": user_id, "d": price, "r": str(order_id)})


_last_live_sync: dict[int, float] = {}
LIVE_SYNC_EVERY = 10.0      # seconds, per user


async def _live_sync(user_id: int) -> None:
    """Pull fresh statuses for this user's open orders from the provider, at most every 10s,
    without making the page wait more than a few seconds."""
    now = time.monotonic()
    if now - _last_live_sync.get(user_id, 0) < LIVE_SYNC_EVERY:
        return
    _last_live_sync[user_id] = now
    try:
        await asyncio.wait_for(sync_user_orders(user_id), timeout=6)
    except Exception:
        log.warning("live sync for user %s skipped", user_id, exc_info=True)


@router.get("")
async def list_orders(status: str | None = None, q: str | None = None,
                      limit: int = 50, offset: int = 0, user: dict = Depends(current_user)):
    await _live_sync(user["id"])
    limit = max(1, min(limit, 100))
    where = ["o.user_id = :u"]
    params: dict = {"u": user["id"], "lim": limit, "off": offset}
    if status:
        where.append("o.status = :st")
        params["st"] = status
    if q:
        where.append("(o.id::text = :q or o.link ilike :ql)")
        params.update(q=q.lstrip("#"), ql=f"%{q}%")

    async with transaction() as db:
        rows = await db.fetch_all(f"""
            select o.id, o.created_at, o.link, o.quantity, o.remains, o.start_count, o.price_php,
                   o.status, o.completed_at, s.name as service_name, s.tier, s.refill_days,
                   r.status as refill_status,
                   case
                     when s.refill_days = 0 then 'none'
                     when r.status = 'pending' then 'requested'
                     when o.status <> 'completed' then 'after_completion'
                     when o.completed_at + make_interval(days => s.refill_days) < now() then 'expired'
                     else 'available'
                   end as refill_state,
                   coalesce((select sum(l.delta) from ledger l
                              where l.reason = 'refund' and l.ref = o.id::text), 0) as refunded_php
              from orders o
              join services s on s.id = o.service_id
              left join lateral (
                select status from provider_refills pr
                 where pr.order_id = o.id order by pr.requested_at desc limit 1
              ) r on true
             where {' and '.join(where)}
             order by o.created_at desc
             limit :lim offset :off
        """, params)
    return rows


@router.post("/{order_id}/refill")
async def request_refill(order_id: int, user: dict = Depends(current_user)):
    async with transaction() as db:
        row = await db.fetch_one("""
            select o.status, o.provider_id, o.provider_order_id, o.completed_at, s.refill_days,
                   p.api_url, p.api_key_env
              from orders o
              join services s on s.id = o.service_id
              join providers p on p.id = o.provider_id
             where o.id = :id and o.user_id = :u
        """, {"id": order_id, "u": user["id"]})
        if not row:
            raise HTTPException(404, "Order not found")
        if row["refill_days"] == 0:
            raise HTTPException(400, "This service has no refill")
        if row["status"] != "completed":
            raise HTTPException(400, "Refill is available after the order completes")
        if not row["completed_at"] or \
                row["completed_at"] + timedelta(days=row["refill_days"]) < datetime.now(timezone.utc):
            raise HTTPException(400, "Refill period has ended")
        pending = await db.fetch_val(
            "select 1 from provider_refills where order_id = :id and status = 'pending'", {"id": order_id}
        )
        if pending:
            raise HTTPException(409, "A refill is already in progress")

    provider = {k: row[k] for k in ("api_url", "api_key_env")}
    try:
        refill_id = await SMMClient.for_provider(provider).refill(row["provider_order_id"])
    except ProviderError as e:
        raise HTTPException(400, f"Refill not accepted: {e}")

    async with transaction() as db:
        await db.execute("""
            insert into provider_refills (order_id, provider_id, provider_refill_id)
            values (:o, :p, :r) on conflict do nothing
        """, {"o": order_id, "p": row["provider_id"], "r": refill_id})
    return {"ok": True, "refill_id": refill_id}
