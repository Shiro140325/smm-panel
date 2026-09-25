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


def _is_custom(svc: dict) -> bool:
    return (svc["type"] or "").strip().lower() == CUSTOM_COMMENTS


@router.post("")
async def create_order(body: OrderIn, user: dict = Depends(current_user)):
    return await place_order(user["id"], body.service_id, body.link, body.quantity, body.comments)


async def place_order(user_id: int, service_id: int, link: str, quantity: int, comments_text: str | None = None) -> dict:
    """Validate, charge and place one order. Raises HTTPException with a customer-facing message."""
    # 1) validate, price, debit balance, record order — one transaction, user row locked
    async with transaction() as db:
        svc = await db.fetch_one(SERVICE_SELECT + " and s.id = :id", {"id": service_id})
        if not svc:
            raise HTTPException(404, "Service not found")

        comments = None
        if _is_custom(svc):
            lines = [ln.strip() for ln in (comments_text or "").splitlines() if ln.strip()]
            if not lines:
                raise HTTPException(400, "Enter at least one comment, one per line")
            quantity, comments = len(lines), "\n".join(lines)   # quantity = number of comments

        if not svc["min_qty"] <= quantity <= svc["max_qty"]:
            raise HTTPException(400, f"Quantity must be between {svc['min_qty']} and {svc['max_qty']}")

        per_1k = price_per_1k_php(svc["rate"], svc["currency"], svc["markup_pct"])
        price = order_price_php(per_1k, quantity)

        await db.execute("select id from users where id = :u for update", {"u": user_id})
        if await balance_of(db, user_id) < price:
            raise HTTPException(402, "Not enough balance")

        order = await db.fetch_one("""
            insert into orders (user_id, service_id, provider_id, link, quantity, price_php, comments)
            values (:u, :s, :p, :l, :q, :price, :c) returning id
        """, {"u": user_id, "s": svc["id"], "p": svc["provider_id"], "l": link,
              "q": quantity, "price": price, "c": comments})
        await db.execute(
            "insert into ledger (user_id, delta, reason, ref) values (:u, :d, 'order', :r)",
            {"u": user_id, "d": -price, "r": str(order["id"])},
        )
        provider = await db.fetch_one("select * from providers where id = :p", {"p": svc["provider_id"]})

    # 2) place upstream — outside the transaction
    try:
        client = SMMClient.for_provider(provider)
        extra = {"comments": comments} if comments else {}
        provider_order_id = await client.add(svc["provider_service_id"], link, quantity, **extra)
    except ProviderError as e:
        # provider rejected it: mark failed and refund in full
        async with transaction() as db:
            await _fail_and_refund(db, order["id"], user_id, price)
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


# ------------------------------------------------------------------ mass order

MASS_MAX_LINES = 100
MASS_CONCURRENCY = 5


class MassIn(BaseModel):
    orders: str = Field(max_length=100_000)   # one "service_id|link|quantity" per line


def parse_mass(text: str) -> tuple[list[dict], list[dict]]:
    """→ (rows, errors). Blank lines are skipped; line numbers are 1-based as the user sees them."""
    rows, errors = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            errors.append({"line": n, "error": "Use the format service_id|link|quantity"}); continue
        sid, link, qty = parts
        if not sid.lstrip("#").isdigit():
            errors.append({"line": n, "error": f"Service ID must be a number (got “{sid}”)"}); continue
        if not link.startswith(("https://", "http://")) or len(link) > 500:
            errors.append({"line": n, "error": "Link must start with https://"}); continue
        q = qty.replace(",", "")
        if not q.isdigit() or int(q) <= 0:
            errors.append({"line": n, "error": f"Quantity must be a whole number (got “{qty}”)"}); continue
        rows.append({"line": n, "service_id": int(sid.lstrip("#")), "link": link, "quantity": int(q)})
    return rows, errors


@router.post("/mass")
async def mass_order(body: MassIn, user: dict = Depends(current_user)):
    rows, errors = parse_mass(body.orders)
    if not rows and not errors:
        raise HTTPException(400, "Add at least one order, one per line")
    if len(rows) + len(errors) > MASS_MAX_LINES:
        raise HTTPException(400, f"Up to {MASS_MAX_LINES} orders at a time")

    # validate everything up front: nothing is placed if any line is wrong
    async with transaction() as db:
        svcs = {r["id"]: r for r in await db.fetch_all(
            SERVICE_SELECT + " and s.id = ANY(CAST(:ids AS int[]))",
            {"ids": sorted({r["service_id"] for r in rows}) or [0]})}
        balance = await balance_of(db, user["id"])
    total = 0.0
    for r in rows:
        svc = svcs.get(r["service_id"])
        if not svc:
            errors.append({"line": r["line"], "error": f"Unknown service ID {r['service_id']}"}); continue
        if _is_custom(svc):
            errors.append({"line": r["line"], "error": "Custom comments can't be mass ordered. Use New order"}); continue
        if not svc["min_qty"] <= r["quantity"] <= svc["max_qty"]:
            errors.append({"line": r["line"], "error": f"Quantity must be between {svc['min_qty']:,} and {svc['max_qty']:,}"}); continue
        total += order_price_php(price_per_1k_php(svc["rate"], svc["currency"], svc["markup_pct"]), r["quantity"])
    if errors:
        errors.sort(key=lambda e: e["line"])
        shown = "; ".join(f"Line {e['line']}: {e['error']}" for e in errors[:5])
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        raise HTTPException(400, shown + more)
    if round(total, 2) > balance:
        raise HTTPException(402, f"Not enough balance: these orders cost ₱{total:,.2f}, you have ₱{balance:,.2f}")

    # place them, a few at a time; each order is charged and refunded independently
    sem = asyncio.Semaphore(MASS_CONCURRENCY)

    async def one(r):
        async with sem:
            try:
                res = await place_order(user["id"], r["service_id"], r["link"], r["quantity"])
                return {"line": r["line"], "ok": True, "order_id": res["id"], "charge_php": res["charge_php"]}
            except HTTPException as e:
                return {"line": r["line"], "ok": False, "error": str(e.detail)}

    results = await asyncio.gather(*(one(r) for r in rows))
    ok = [r for r in results if r["ok"]]
    return {"results": results, "placed": len(ok), "failed": len(results) - len(ok),
            "charged_php": round(sum(r["charge_php"] for r in ok), 2)}


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
                              where l.reason = 'refund' and l.ref = o.id::text), 0) as refunded_php,
                   (o.status in ('pending', 'in_progress') and o.cancel_requested_at is not null) as cancel_requested,
                   (o.status in ('pending', 'in_progress') and o.cancel_requested_at is null
                     and o.provider_order_id is not null and coalesce(ps.cancel, false)) as can_cancel
              from orders o
              join services s on s.id = o.service_id
              left join provider_services ps
                on ps.provider_id = o.provider_id and ps.provider_service_id = s.provider_service_id
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


@router.post("/{order_id}/cancel")
async def request_cancel(order_id: int, user: dict = Depends(current_user)):
    """Ask the provider to cancel a running order. Nothing is refunded here: when the provider
    reports it canceled (or partial), the status sync refunds the undelivered part."""
    async with transaction() as db:
        row = await db.fetch_one("""
            select o.status, o.provider_order_id, o.cancel_requested_at, ps.cancel,
                   p.api_url, p.api_key_env
              from orders o
              join services s on s.id = o.service_id
              join providers p on p.id = o.provider_id
              left join provider_services ps
                on ps.provider_id = o.provider_id and ps.provider_service_id = s.provider_service_id
             where o.id = :id and o.user_id = :u
        """, {"id": order_id, "u": user["id"]})
    if not row:
        raise HTTPException(404, "Order not found")
    if row["status"] not in ("pending", "in_progress") or not row["provider_order_id"]:
        raise HTTPException(400, "Only pending or in-progress orders can be canceled")
    if not row["cancel"]:
        raise HTTPException(400, "This service can't be canceled once placed")
    if row["cancel_requested_at"]:
        raise HTTPException(409, "Cancel already requested")

    provider = {k: row[k] for k in ("api_url", "api_key_env")}
    try:
        res = await SMMClient.for_provider(provider).cancel([row["provider_order_id"]])
    except ProviderError as e:
        raise HTTPException(400, f"Cancel not accepted: {e}")
    # API v2: [{"order": 123, "cancel": 1}] or [{"order": 123, "cancel": {"error": "..."}}]
    item = next((x for x in (res if isinstance(res, list) else [])
                 if str(x.get("order")) == str(row["provider_order_id"])), None)
    result = (item or {}).get("cancel")
    if isinstance(result, dict) or not result:
        err = result.get("error") if isinstance(result, dict) else "no answer from provider"
        raise HTTPException(400, f"Cancel not accepted: {err}")

    async with transaction() as db:
        await db.execute("update orders set cancel_requested_at = now(), updated_at = now() where id = :id",
                         {"id": order_id})
    return {"ok": True}
