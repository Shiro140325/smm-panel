import asyncio
import logging
import json
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app import order_queue
from app.db import transaction
from app.pricing import SERVICE_SELECT, order_price_php, price_per_1k_php
from app.providers.smm_client import ProviderError, SMMClient
from app.security import balance_of, current_user
from app.tiers import current_tier, discounted_per_1k
from app.ratelimit import client_ip
from app.trial import TRIAL_QTY, device_of, trial_for
from app.workers.sync import sync_user_orders

log = logging.getLogger("orders")
router = APIRouter(prefix="/orders", tags=["orders"])

OPEN_STATUSES = ("queued", "creating", "pending", "in_progress")


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
async def create_order(body: OrderIn, request: Request, user: dict = Depends(current_user)):
    return await place_order(user["id"], body.service_id, body.link, body.quantity, body.comments,
                             trial_ctx={"device": device_of(request), "ip": client_ip(request)})


async def place_order(user_id: int, service_id: int, link: str, quantity: int, comments_text: str | None = None,
                      source: str = "web", trial_ctx: dict | None = None) -> dict:
    """Validate, charge and place one order. Raises HTTPException with a customer-facing message.
    Website orders (source "web") get the customer's tier discount; reseller API orders don't."""
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

        per_1k = price_per_1k_php(svc["rate"], svc["currency"], svc["markup_pct"], svc["price_php"])
        if source == "web":
            per_1k = discounted_per_1k(per_1k, (await current_tier(db, user_id))["discount_pct"])
        price = order_price_php(per_1k, quantity)

        await db.execute("select id from users where id = :u for update", {"u": user_id})
        # the free trial: this account's first order of the trial service, up to the trial quantity
        if source == "web" and quantity <= TRIAL_QTY and trial_ctx is not None:   # single website orders only
            t = await trial_for(db, user_id, trial_ctx["device"], trial_ctx["ip"])
            if t["available"] and svc["id"] == t["service_id"]:
                if await db.fetch_val("select 1 from orders where source = 'trial' and lower(link) = lower(:l) limit 1",
                                      {"l": link}):
                    raise HTTPException(400, "This link already had a free trial. Use your free trial on a different post or video.")
                await db.execute("update users set trial_used_at = now(), trial_device = :d, trial_ip = :ip where id = :u",
                                 {"u": user_id, "d": trial_ctx["device"], "ip": trial_ctx["ip"]})
                price, source = 0.0, "trial"
        if await balance_of(db, user_id) < price:
            raise HTTPException(402, "Not enough balance")

        # sending paused (or a backlog still going out): charge now, send later from the queue
        queued = await order_queue.should_queue(db)
        order = await db.fetch_one("""
            insert into orders (user_id, service_id, provider_id, link, quantity, price_php, comments, source, status)
            values (:u, :s, :p, :l, :q, :price, :c, :src, :st) returning id
        """, {"u": user_id, "s": svc["id"], "p": svc["provider_id"], "l": link,
              "q": quantity, "price": price, "c": comments, "src": source, "st": "queued" if queued else "creating"})
        if price > 0:
            await db.execute(
                "insert into ledger (user_id, delta, reason, ref) values (:u, :d, 'order', :r)",
                {"u": user_id, "d": -price, "r": str(order["id"])},
            )
        provider = await db.fetch_one("select * from providers where id = :p", {"p": svc["provider_id"]})

    result = {"id": order["id"], "status": "pending", "quantity": quantity, "charge_php": price, "free_trial": source == "trial"}
    if queued:
        kick_queue()   # no-op while paused; otherwise makes sure the backlog is moving
        return {**result, "queued": True}

    # 2) place upstream — outside the transaction
    outcome, err = await _submit(order["id"])
    if outcome == "failed":
        raise HTTPException(400, f"Order rejected by provider: {err}")
    if outcome == "needs_review":
        raise HTTPException(502, "Couldn't confirm the order with our provider. Support will check it.")
    return result


# ------------------------------------------------------------------ Messenger bot menu orders

async def chat_item(db, item_id: int) -> dict | None:
    """A bot menu item with its SMMGen service (min, max, type, cost), or None if it can't be ordered."""
    return await db.fetch_one("""
        select m.id, m.name, m.prices, m.provider_service_id, m.active,
               p.id as provider_id, ps.name as provider_name, ps.min_qty, ps.max_qty, ps.rate, ps.type, p.currency
          from bot_menu m
          join providers p on p.active
          join provider_services ps on ps.provider_id = p.id and ps.provider_service_id = m.provider_service_id
         where m.id = :id
         order by p.id limit 1
    """, {"id": item_id})


def chat_price(item: dict, quantity: int) -> float:
    """The owner's price for this many, from the item's price list (see app/bot/pricing.py)."""
    from app.bot import pricing
    prices = item["prices"] if not isinstance(item["prices"], str) else json.loads(item["prices"])
    return pricing.price_for(prices, quantity)


async def place_chat_order(user_id: int, item_id: int, link: str, quantity: int, comments_text: str | None = None) -> dict:
    """Charge and place a Messenger order for a bot menu item: the owner's price, sent straight to the
    item's SMMGen service. Same queue, status sync, refunds and cancellations as website orders."""
    async with transaction() as db:
        item = await chat_item(db, item_id)
        if not item or not item["active"]:
            raise HTTPException(404, "That item isn't available right now")
        comments = None
        if (item["type"] or "").strip().lower() == CUSTOM_COMMENTS:
            lines = [ln.strip() for ln in (comments_text or "").splitlines() if ln.strip()]
            if not lines:
                raise HTTPException(400, "Send the comments, one per line")
            quantity, comments = len(lines), "\n".join(lines)   # quantity = number of comments
        if not item["min_qty"] <= quantity <= item["max_qty"]:
            raise HTTPException(400, f"Quantity must be between {item['min_qty']:,} and {item['max_qty']:,}")
        price = chat_price(item, quantity)
        # the site record for this SMMGen service (every one is imported; make a hidden one if not)
        sid = await db.fetch_val("""select id from services where provider_id = :p and provider_service_id = :ps
                                     order by auto desc, id limit 1""", {"p": item["provider_id"], "ps": item["provider_service_id"]})
        if not sid:
            sid = await db.fetch_val("""insert into services (provider_id, provider_service_id, platform, category, name, tier, hidden)
                                        values (:p, :ps, 'other', 'Chat', :n, 'Chat', true) returning id""",
                                     {"p": item["provider_id"], "ps": item["provider_service_id"], "n": item["name"]})
        await db.execute("select id from users where id = :u for update", {"u": user_id})
        if await balance_of(db, user_id) < price:
            raise HTTPException(402, "Not enough balance")
        queued = await order_queue.should_queue(db)
        order = await db.fetch_one("""
            insert into orders (user_id, service_id, provider_id, link, quantity, price_php, comments, source, status, label)
            values (:u, :s, :p, :l, :q, :price, :c, 'chat', :st, :label) returning id
        """, {"u": user_id, "s": sid, "p": item["provider_id"], "l": link, "q": quantity, "price": price, "c": comments,
              "st": "queued" if queued else "creating", "label": item["name"]})
        await db.execute("insert into ledger (user_id, delta, reason, ref) values (:u, :d, 'order', :r)",
                         {"u": user_id, "d": -price, "r": str(order["id"])})
    result = {"id": order["id"], "status": "pending", "quantity": quantity, "charge_php": price}
    if queued:
        kick_queue()
        return {**result, "queued": True}
    outcome, err = await _submit(order["id"])
    if outcome == "failed":
        raise HTTPException(400, f"Order rejected: {err}")
    if outcome == "needs_review":
        raise HTTPException(502, "Couldn't confirm the order. Support will check it.")
    return result


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
        discount = (await current_tier(db, user["id"]))["discount_pct"]
    total = 0.0
    for r in rows:
        svc = svcs.get(r["service_id"])
        if not svc:
            errors.append({"line": r["line"], "error": f"Unknown service ID {r['service_id']}"}); continue
        if _is_custom(svc):
            errors.append({"line": r["line"], "error": "Custom comments can't be mass ordered. Use New order"}); continue
        if not svc["min_qty"] <= r["quantity"] <= svc["max_qty"]:
            errors.append({"line": r["line"], "error": f"Quantity must be between {svc['min_qty']:,} and {svc['max_qty']:,}"}); continue
        total += order_price_php(discounted_per_1k(
            price_per_1k_php(svc["rate"], svc["currency"], svc["markup_pct"], svc["price_php"]), discount), r["quantity"])
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


async def _submit(order_id: int) -> tuple[str, str | None]:
    """Send one charged order (status 'creating') to its provider. Returns ('pending', None),
    ('failed', reason) after a full refund, or ('needs_review', None) when we can't tell if it was placed."""
    async with transaction() as db:
        o = await db.fetch_one("""
            select o.id, o.user_id, o.price_php, o.source, o.link, o.quantity, o.comments, o.provider_id,
                   s.provider_service_id
              from orders o join services s on s.id = o.service_id where o.id = :id
        """, {"id": order_id})
        provider = await db.fetch_one("select * from providers where id = :p", {"p": o["provider_id"]})
    try:
        client = SMMClient.for_provider(provider)
        extra = {"comments": o["comments"]} if o["comments"] else {}
        provider_order_id = await client.add(o["provider_service_id"], o["link"], o["quantity"], **extra)
    except ProviderError as e:
        # provider rejected it: mark failed and refund in full
        async with transaction() as db:
            await _fail_and_refund(db, o["id"], o["user_id"], float(o["price_php"]))
            if o["source"] == "trial":   # the trial wasn't delivered: give it back
                await db.execute("update users set trial_used_at = null, trial_device = null, trial_ip = null where id = :u",
                                 {"u": o["user_id"]})
        return "failed", str(e)
    except Exception:
        # network error/timeout: we can't know whether it was placed — never auto-refund
        log.exception("provider add failed for order %s", o["id"])
        async with transaction() as db:
            await db.execute("update orders set status = 'needs_review', updated_at = now() where id = :id", {"id": o["id"]})
        return "needs_review", None
    async with transaction() as db:
        await db.execute("""
            update orders set provider_order_id = :po, status = 'pending', updated_at = now()
            where id = :id
        """, {"po": provider_order_id, "id": o["id"]})
    return "pending", None


QUEUE_GAP_SECONDS = 1.0
_drain_lock = asyncio.Lock()


async def drain_queue() -> int:
    """Send queued orders, oldest first, one per QUEUE_GAP_SECONDS, until the queue is empty or
    sending is paused again. Only one runs at a time."""
    if _drain_lock.locked():
        return 0
    sent = 0
    async with _drain_lock:
        while True:
            async with transaction() as db:
                if await order_queue.is_paused(db):
                    break
                oid = await db.fetch_val("""
                    update orders set status = 'creating', updated_at = now()
                     where id = (select id from orders where status = 'queued' order by id limit 1 for update skip locked)
                    returning id
                """)
            if not oid:
                break
            try:
                outcome, err = await _submit(oid)
                log.info("queued order %s sent: %s%s", oid, outcome, f" ({err})" if err else "")
            except Exception:
                log.exception("queued order %s: send failed", oid)
            sent += 1
            await asyncio.sleep(QUEUE_GAP_SECONDS)
    return sent


def kick_queue() -> None:
    """Start sending the backlog in the background (if it isn't already)."""
    if not _drain_lock.locked():
        asyncio.get_running_loop().create_task(drain_queue())


async def _fail_and_refund(db, order_id: int, user_id: int, price: float):
    await db.execute(
        "update orders set status = 'failed', updated_at = now() where id = :id", {"id": order_id}
    )
    if price > 0:
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



# Order list filters: a real status, or one of these views (shared with /admin).
ORDER_VIEWS = {
    # a refill was asked for and the provider hasn't finished it yet
    "refilling": "exists (select 1 from provider_refills pr where pr.order_id = o.id and pr.status = 'pending')",
    # money went back to the customer for this order (failed, canceled or partial)
    "refunded": "exists (select 1 from ledger l where l.reason = 'refund' and l.ref = o.id::text)",
}


def order_filter(status: str, where: list, params: dict) -> None:
    if status == "pending":   # queued orders look pending to customers
        where.append("o.status in ('pending', 'queued')")
    elif status in ORDER_VIEWS:
        where.append(ORDER_VIEWS[status])
    else:
        where.append("o.status = :st")
        params["st"] = status


@router.get("")
async def list_orders(status: str | None = None, q: str | None = None,
                      limit: int = 50, offset: int = 0, user: dict = Depends(current_user)):
    await _live_sync(user["id"])
    limit = max(1, min(limit, 100))
    where = ["o.user_id = :u"]
    params: dict = {"u": user["id"], "lim": limit, "off": offset}
    if status:
        order_filter(status, where, params)
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
                   (o.status in ('pending', 'in_progress', 'needs_review') and o.cancel_requested_at is not null
                     and o.cancel_declined_at is null) as cancel_requested,
                   (o.status in ('pending', 'in_progress', 'needs_review') and o.cancel_declined_at is not null) as cancel_declined,
                   (o.status in ('queued', 'pending', 'in_progress', 'needs_review')
                     and o.cancel_requested_at is null and o.cancel_declined_at is null) as can_cancel
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


# Recently completed, across all customers: what was delivered and how fast, never who or where
# (no links, emails or order owners). Shared by everyone, so it's cached briefly.
RECENT_LIMIT = 30
_recent: tuple[float, list] = (0.0, [])


@router.get("/recently-completed")
async def recently_completed(user: dict = Depends(current_user)):
    global _recent
    if time.monotonic() - _recent[0] >= 60:
        async with transaction() as db:
            rows = await db.fetch_all(f"""
                select s.platform, s.category, s.name as service_name, s.tier, o.quantity, o.completed_at,
                       extract(epoch from (o.completed_at - o.created_at))::int as took_seconds
                  from orders o join services s on s.id = o.service_id
                 where o.status = 'completed' and o.completed_at is not null
                 order by o.completed_at desc
                 limit {RECENT_LIMIT}
            """)
        _recent = (time.monotonic(), rows)
    return _recent[1]


@router.post("/{order_id}/refill")
async def request_refill(order_id: int, user: dict = Depends(current_user)):
    return {"ok": True, "refill_id": await refill_order(order_id, user["id"])}


async def refill_order(order_id: int, user_id: int) -> int:
    """Ask the provider to refill a completed order. Returns our refill id (provider_refills.id)."""
    async with transaction() as db:
        row = await db.fetch_one("""
            select o.status, o.provider_id, o.provider_order_id, o.completed_at, s.refill_days,
                   p.api_url, p.api_key_env
              from orders o
              join services s on s.id = o.service_id
              join providers p on p.id = o.provider_id
             where o.id = :id and o.user_id = :u
        """, {"id": order_id, "u": user_id})
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
        rid = await db.fetch_val("""
            insert into provider_refills (order_id, provider_id, provider_refill_id)
            values (:o, :p, :r) on conflict do nothing returning id
        """, {"o": order_id, "p": row["provider_id"], "r": refill_id})
        if rid is None:   # a parallel request got there first
            rid = await db.fetch_val(
                "select id from provider_refills where order_id = :o and status = 'pending'", {"o": order_id})
    return rid


@router.post("/{order_id}/cancel")
async def request_cancel(order_id: int, user: dict = Depends(current_user)):
    return {"ok": True, "message": await cancel_order(order_id, user["id"])}


CANCELABLE = ("queued", "pending", "in_progress", "needs_review")   # not partial, canceled, completed or failed
MSG_REQUESTED = "Cancel requested. Anything not delivered is refunded to your balance once it's canceled."


async def cancel_order(order_id: int, user_id: int) -> str:
    """Cancel an order. Still queued (never sent) → canceled here with a full refund. Otherwise the
    provider's cancel API when the service supports it; when it doesn't (or refuses), the request goes
    to the owner's "Cancellation pending" list to ask the provider's support by hand. Either way the
    refund comes from the status sync once the provider reports the order canceled or partial.
    Returns the message for the customer."""
    async with transaction() as db:
        row = await db.fetch_one("""
            select o.status, o.provider_order_id, o.cancel_requested_at, o.cancel_declined_at, ps.cancel,
                   p.api_url, p.api_key_env
              from orders o
              join services s on s.id = o.service_id
              join providers p on p.id = o.provider_id
              left join provider_services ps
                on ps.provider_id = o.provider_id and ps.provider_service_id = s.provider_service_id
             where o.id = :id and o.user_id = :u
        """, {"id": order_id, "u": user_id})
    if not row:
        raise HTTPException(404, "Order not found")
    if row["status"] == "queued":   # still waiting to be sent: cancel it here, full refund
        async with transaction() as db:
            o = await db.fetch_one("update orders set status = 'canceled', updated_at = now() "
                                   "where id = :id and status = 'queued' returning price_php, source", {"id": order_id})
            if not o:
                raise HTTPException(409, "This order was just sent. Try again in a moment.")
            if float(o["price_php"]) > 0:
                await db.execute("insert into ledger (user_id, delta, reason, ref) values (:u, :d, 'refund', :r) "
                                 "on conflict do nothing", {"u": user_id, "d": float(o["price_php"]), "r": str(order_id)})
            if o["source"] == "trial":
                await db.execute("update users set trial_used_at = null, trial_device = null, trial_ip = null where id = :u",
                                 {"u": user_id})
        return "Order canceled. The full amount is back in your balance."
    if row["status"] not in CANCELABLE:
        raise HTTPException(400, "This order can't be canceled anymore")
    if row["cancel_declined_at"]:
        raise HTTPException(400, "This order couldn't be canceled")
    if row["cancel_requested_at"]:
        raise HTTPException(409, "Cancel already requested")

    via_api = bool(row["cancel"] and row["provider_order_id"] and row["status"] in ("pending", "in_progress"))
    if via_api:
        provider = {k: row[k] for k in ("api_url", "api_key_env")}
        try:
            res = await SMMClient.for_provider(provider).cancel([row["provider_order_id"]])
            # API v2: [{"order": 123, "cancel": 1}] or [{"order": 123, "cancel": {"error": "..."}}]
            item = next((x for x in (res if isinstance(res, list) else [])
                         if str(x.get("order")) == str(row["provider_order_id"])), None)
            result = (item or {}).get("cancel")
            via_api = bool(result) and not isinstance(result, dict)
            if not via_api:
                log.info("provider refused cancel of order %s: %s", order_id, result)
        except Exception as e:   # refused or unreachable: ask by hand instead
            log.info("provider cancel failed for order %s: %s", order_id, e)
            via_api = False
    async with transaction() as db:
        # manual = the owner has to ask the provider's support (listed under Cancellation pending)
        await db.execute("update orders set cancel_requested_at = now(), cancel_manual = :m, updated_at = now() "
                         "where id = :id", {"id": order_id, "m": not via_api})
    return MSG_REQUESTED
