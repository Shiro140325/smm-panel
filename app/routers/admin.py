"""Owner control panel API (/admin/api/*), unlocked with the ADMIN_PASS env var.

Separate from customer accounts: its own short-lived cookie scoped to /admin, SameSite=Strict
(so no other site can make the browser send it), and a per-IP lockout after repeated wrong passwords.
"""
import hmac
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import fx
from app.config import get_settings
from app.db import DB, get_db
from app.providers.smm_client import SMMClient
from app.ratelimit import client_ip
from app.routers import services as services_router
from app.routers.orders import _fail_and_refund, order_filter

router = APIRouter(prefix="/admin/api", tags=["admin"])
log = logging.getLogger("admin")

COOKIE = "admin_session"
TTL_HOURS = 12
MAX_FAILS, LOCK_SECONDS = 5, 15 * 60
_fails: dict[str, list[float]] = {}   # ip → timestamps of recent wrong passwords


def _admin_pass() -> str:
    return os.environ.get("ADMIN_PASS", "")


_ip = client_ip


def require_admin(request: Request) -> None:
    if not _admin_pass():
        raise HTTPException(503, "Admin panel is off: set ADMIN_PASS on the server")
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not logged in")
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired")
    if payload.get("adm") != 1:
        raise HTTPException(401, "Not logged in")


class LoginIn(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    expected = _admin_pass()
    if not expected:
        raise HTTPException(503, "Admin panel is off: set ADMIN_PASS on the server")
    ip, now = _ip(request), time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
    if len(recent) >= MAX_FAILS:
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        _fails[ip] = recent + [now]
        log.warning("admin login failed from %s", ip)
        raise HTTPException(401, "Wrong password")
    _fails.pop(ip, None)
    s = get_settings()
    token = jwt.encode({"adm": 1, "exp": datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS)},
                       s.jwt_secret, algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=TTL_HOURS * 3600, path="/admin")
    log.info("admin login from %s", ip)
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/admin")
    return {"ok": True}


@router.get("/me", dependencies=[Depends(require_admin)])
async def me():
    return {"ok": True}


@router.get("/overview", dependencies=[Depends(require_admin)])
async def overview(db: DB = Depends(get_db)):
    money = await db.fetch_one("""
        select
          (select count(*) from users) as customers,
          (select count(*) from users where created_at > now() - interval '7 days') as customers_7d,
          (select coalesce(sum(delta), 0) from ledger) as balances_php,
          (select coalesce(sum(amount_php), 0) from topups where status = 'credited' and credited_at >= date_trunc('day', now() at time zone 'Asia/Manila') at time zone 'Asia/Manila') as topups_today,
          (select coalesce(sum(amount_php), 0) from topups where status = 'credited' and credited_at > now() - interval '7 days') as topups_7d,
          (select coalesce(sum(amount_php), 0) from topups where status = 'credited') as topups_all,
          (select count(*) from orders where status = 'needs_review') as needs_review
    """)
    sales = await db.fetch_all("""
        with o as (
          select o.*, coalesce((select sum(l.delta) from ledger l where l.reason = 'refund' and l.ref = o.id::text), 0) as refunded
            from orders o where o.status not in ('failed', 'creating')
        )
        select span,
               count(*) as orders,
               coalesce(sum(price_php - refunded), 0) as revenue_php,
               coalesce(sum(cost), 0) as cost_provider,
               count(*) filter (where cost is null) as cost_unknown
          from o, lateral (values ('today', created_at >= date_trunc('day', now() at time zone 'Asia/Manila') at time zone 'Asia/Manila'),
                                  ('7d', created_at > now() - interval '7 days'),
                                  ('all', true)) v(span, inside)
         where inside
         group by span
    """)
    rate = fx.usd_to_php_raw()
    by_span = {r["span"]: {"orders": r["orders"], "revenue_php": float(r["revenue_php"]),
                           "cost_php": round(float(r["cost_provider"]) * rate, 2),
                           "cost_unknown": r["cost_unknown"]} for r in sales}
    for v in by_span.values():
        v["profit_php"] = round(v["revenue_php"] - v["cost_php"], 2)

    providers = []
    for p in await db.fetch_all("select id, name, api_url, api_key_env, currency from providers where active order by id"):
        try:
            b = await SMMClient.for_provider(p).balance()
            providers.append({"name": p["name"], "balance": float(b.get("balance") or 0),
                              "currency": b.get("currency") or p["currency"]})
        except Exception as e:
            providers.append({"name": p["name"], "error": str(e)[:120]})
    return {**{k: (float(v) if k == "balances_php" else v) for k, v in dict(money).items()},
            "sales": by_span, "providers": providers, "usd_to_php": rate}


@router.get("/orders", dependencies=[Depends(require_admin)])
async def orders(status: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
                 db: DB = Depends(get_db)):
    where, params = ["true"], {"lim": max(1, min(limit, 200)), "off": max(0, offset)}
    if status:
        order_filter(status, where, params)
    if q:
        where.append("(o.id::text = :q or o.provider_order_id::text = :q or u.email ilike :ql or o.link ilike :ql)")
        params.update(q=q.strip().lstrip("#"), ql=f"%{q.strip()}%")
    return await db.fetch_all(f"""
        select o.id, o.created_at, o.status, o.quantity, o.remains, o.price_php, o.cost, o.link,
               o.provider_order_id, o.cancel_requested_at, u.email, s.name as service_name, s.tier,
               (select pr.status from provider_refills pr where pr.order_id = o.id order by pr.requested_at desc limit 1) as refill_status,
               coalesce((select sum(l.delta) from ledger l where l.reason = 'refund' and l.ref = o.id::text), 0) as refunded_php
          from orders o join users u on u.id = o.user_id join services s on s.id = o.service_id
         where {' and '.join(where)}
         order by o.created_at desc limit :lim offset :off
    """, params)


@router.post("/orders/{order_id}/refund", dependencies=[Depends(require_admin)])
async def refund_order(order_id: int, db: DB = Depends(get_db)):
    """For orders stuck in 'Under review' (the provider call failed mid-way): after checking the
    provider's dashboard that it wasn't placed, mark it failed and refund in full."""
    o = await db.fetch_one("select id, user_id, price_php, status from orders where id = :id for update", {"id": order_id})
    if not o:
        raise HTTPException(404, "Order not found")
    if o["status"] != "needs_review":
        raise HTTPException(400, "Only orders under review can be refunded here")
    await _fail_and_refund(db, o["id"], o["user_id"], float(o["price_php"]))
    log.info("admin refunded order %s", order_id)
    return {"ok": True}


@router.get("/users", dependencies=[Depends(require_admin)])
async def users(q: str | None = None, limit: int = 50, offset: int = 0, db: DB = Depends(get_db)):
    params = {"lim": max(1, min(limit, 200)), "off": max(0, offset)}
    where = "true"
    if q:
        where = "(u.email ilike :ql or u.id::text = :q)"
        params.update(q=q.strip().lstrip("#"), ql=f"%{q.strip()}%")
    return await db.fetch_all(f"""
        select u.id, u.email, u.created_at,
               coalesce((select sum(delta) from ledger l where l.user_id = u.id), 0) as balance_php,
               (select count(*) from orders o where o.user_id = u.id) as orders,
               coalesce((select sum(amount_php) from topups t where t.user_id = u.id and t.status = 'credited'), 0) as topped_up_php
          from users u where {where}
         order by u.created_at desc limit :lim offset :off
    """, params)


class AdjustIn(BaseModel):
    amount_php: float = Field(..., description="positive adds, negative removes")
    note: str = Field(..., min_length=3, max_length=200)


@router.post("/users/{user_id}/adjust", dependencies=[Depends(require_admin)])
async def adjust_balance(user_id: int, body: AdjustIn, db: DB = Depends(get_db)):
    amt = round(body.amount_php, 2)
    if amt == 0 or abs(amt) > 100000:
        raise HTTPException(400, "Amount must be non-zero and at most ₱100,000")
    if not await db.fetch_one("select id from users where id = :u for update", {"u": user_id}):
        raise HTTPException(404, "Customer not found")
    bal = float(await db.fetch_val("select coalesce(sum(delta), 0) from ledger where user_id = :u", {"u": user_id}))
    if bal + amt < 0:
        raise HTTPException(400, f"That would make the balance negative (it's ₱{bal:,.2f})")
    await db.execute("insert into ledger (user_id, delta, reason, ref) values (:u, :d, 'adjustment', :r)",
                     {"u": user_id, "d": amt, "r": body.note.strip()[:200]})
    log.info("admin adjusted user %s by %s (%s)", user_id, amt, body.note)
    return {"ok": True, "balance_php": round(bal + amt, 2)}


@router.get("/topups", dependencies=[Depends(require_admin)])
async def topups(status: str | None = None, limit: int = 50, offset: int = 0, db: DB = Depends(get_db)):
    params = {"lim": max(1, min(limit, 200)), "off": max(0, offset)}
    where = "true"
    if status:
        where = "t.status = :st"
        params["st"] = status
    return await db.fetch_all(f"""
        select t.id, t.created_at, t.credited_at, t.amount_php, t.method, t.status, t.checkout_id, u.email
          from topups t join users u on u.id = t.user_id
         where {where} order by t.created_at desc limit :lim offset :off
    """, params)


@router.get("/services", dependencies=[Depends(require_admin)])
async def services(q: str | None = None, platform: str | None = None, hidden: bool = False, limit: int = 50,
                   db: DB = Depends(get_db)):
    where, params = ["s.active", "s.hidden = :hid"], {"hid": hidden, "lim": max(1, min(limit, 200))}
    if platform:
        where.append("s.platform = :pf")
        params["pf"] = platform
    if q:
        where.append("(s.id::text = :q or s.name ilike :ql or s.category ilike :ql or ps.name ilike :ql)")
        params.update(q=q.strip().lstrip("#"), ql=f"%{q.strip()}%")
    return await db.fetch_all(f"""
        select s.id, s.platform, s.category, s.name, s.tier, s.auto, s.hidden, s.provider_service_id,
               ps.name as provider_name, ps.rate, ps.min_qty, ps.max_qty, ps.refill, ps.cancel,
               (select count(*) from orders o where o.service_id = s.id) as orders
          from services s join provider_services ps
            on ps.provider_id = s.provider_id and ps.provider_service_id = s.provider_service_id
         where {' and '.join(where)}
         order by (select count(*) from orders o where o.service_id = s.id) desc, s.id
         limit :lim
    """, params)


class HiddenIn(BaseModel):
    hidden: bool


@router.post("/services/{service_id}/hidden", dependencies=[Depends(require_admin)])
async def set_hidden(service_id: int, body: HiddenIn, db: DB = Depends(get_db)):
    """Hide a service from customers (and block new orders). Separate from `active`, which the
    catalog import manages, so an import never brings a hidden service back."""
    row = await db.fetch_one("update services set hidden = :h where id = :id returning id",
                             {"h": body.hidden, "id": service_id})
    if not row:
        raise HTTPException(404, "Service not found")
    services_router._built.clear()   # customers see the change on their next load
    services_router._cache.clear()
    return {"ok": True}
