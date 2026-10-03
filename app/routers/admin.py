"""Owner control panel API (/admin/api/*), unlocked with the ADMIN_PASS env var.

Separate from customer accounts: its own short-lived cookie scoped to /admin, SameSite=Strict
(so no other site can make the browser send it), and a per-IP lockout after repeated wrong passwords.
"""
import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import announcement, fx, order_queue, tiers, totp
from app.config import get_settings
from app.db import DB, get_db, transaction
from app.providers.smm_client import SMMClient
from app.ratelimit import client_ip
from app.routers import services as services_router
from app.routers import orders as orders_router
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


PENDING = "admin_pending"
PENDING_SECONDS = 5 * 60
SETUP_OPEN = {"/admin/api/me", "/admin/api/totp/setup"}


async def _totp_row(db):
    return await db.fetch_one("select secret, confirmed_at, last_step from admin_totp where id = 1")


async def require_admin(request: Request, db: DB = Depends(get_db)) -> None:
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
    row = await _totp_row(db)
    if row and row["confirmed_at"]:
        if payload.get("mfa") != 1:   # a session from before the authenticator was set up
            raise HTTPException(401, "Log in again with your authenticator code")
    elif request.url.path not in SETUP_OPEN:
        raise HTTPException(403, "Set up the authenticator first")


def _session(response: Response, mfa: bool) -> None:
    s = get_settings()
    token = jwt.encode({"adm": 1, "mfa": 1 if mfa else 0, "exp": datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS)},
                       s.jwt_secret, algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=TTL_HOURS * 3600, path="/admin")


def _locked(ip: str, now: float) -> list[float]:
    recent = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
    if len(recent) >= MAX_FAILS:
        raise HTTPException(429, "Too many wrong tries. Try again in 15 minutes.")
    return recent


def _backup_hash(code: str) -> str:
    norm = "".join(ch for ch in code.lower() if ch.isalnum())
    return hmac.new(get_settings().jwt_secret.encode(), f"admin-backup:{norm}".encode(), hashlib.sha256).hexdigest()


class LoginIn(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: DB = Depends(get_db)):
    """Step 1: the admin password. Then the authenticator code (or, before it's set up, the setup screen)."""
    expected = _admin_pass()
    if not expected:
        raise HTTPException(503, "Admin panel is off: set ADMIN_PASS on the server")
    ip, now = _ip(request), time.time()
    recent = _locked(ip, now)
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        _fails[ip] = recent + [now]
        log.warning("admin login failed from %s", ip)
        raise HTTPException(401, "Wrong password")
    row = await _totp_row(db)
    if row and row["confirmed_at"]:
        s = get_settings()
        token = jwt.encode({"admp": 1, "exp": datetime.now(timezone.utc) + timedelta(seconds=PENDING_SECONDS)},
                           s.jwt_secret, algorithm="HS256")
        response.set_cookie(PENDING, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                            max_age=PENDING_SECONDS, path="/admin")
        return {"totp_required": True}
    _fails.pop(ip, None)
    _session(response, mfa=False)   # only the setup screen opens with this
    log.info("admin login (authenticator not set up yet) from %s", ip)
    return {"ok": True, "setup_required": True}


class CodeIn(BaseModel):
    code: str = Field(max_length=20)


@router.post("/login/totp")
async def login_totp(body: CodeIn, request: Request, response: Response, db: DB = Depends(get_db)):
    """Step 2: the 6-digit code from the authenticator app, or a one-time backup code."""
    try:
        payload = jwt.decode(request.cookies.get(PENDING) or "", get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Your login timed out. Enter the password again.")
    if payload.get("admp") != 1:
        raise HTTPException(401, "Your login timed out. Enter the password again.")
    ip, now = _ip(request), time.time()
    recent = _locked(ip, now)
    row = await db.fetch_one("select secret, confirmed_at, last_step from admin_totp where id = 1 for update")
    ok = False
    if row and row["confirmed_at"]:
        digits = "".join(ch for ch in body.code if ch.isdigit())
        if len(digits) == totp.DIGITS and len(body.code.strip()) <= 7:
            step = totp.check(row["secret"], digits, row["last_step"])
            if step is not None:
                await db.execute("update admin_totp set last_step = :s where id = 1", {"s": step})
                ok = True
        else:   # a backup code, used once
            used = await db.fetch_val("update admin_backup_codes set used_at = now() where code_hash = :h and used_at is null "
                                      "returning code_hash", {"h": _backup_hash(body.code)})
            if used:
                ok = True
                log.warning("admin logged in with a backup code from %s", ip)
    if not ok:
        _fails[ip] = recent + [now]
        log.warning("admin authenticator code wrong from %s", ip)
        raise HTTPException(401, "Wrong code")
    _fails.pop(ip, None)
    response.delete_cookie(PENDING, path="/admin")
    _session(response, mfa=True)
    log.info("admin login from %s", ip)
    return {"ok": True}


@router.get("/totp/setup", dependencies=[Depends(require_admin)])
async def totp_setup(db: DB = Depends(get_db)):
    """The key to add to an authenticator app (kept until confirmed, so a reload shows the same one)."""
    row = await _totp_row(db)
    if row and row["confirmed_at"]:
        raise HTTPException(409, "The authenticator is already set up")
    secret = row["secret"] if row else totp.new_secret()
    if not row:
        await db.execute("insert into admin_totp (id, secret) values (1, :s)", {"s": secret})
    uri = totp.otpauth_uri(secret)
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": uri, "qr_svg": totp.qr_svg(uri)}


BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


@router.post("/totp/setup", dependencies=[Depends(require_admin)])
async def totp_confirm(body: CodeIn, response: Response, db: DB = Depends(get_db)):
    """Confirm with a code from the app. Returns 8 one-time backup codes, shown only now."""
    row = await db.fetch_one("select secret, confirmed_at from admin_totp where id = 1 for update")
    if not row:
        raise HTTPException(400, "Open the setup first")
    if row["confirmed_at"]:
        raise HTTPException(409, "The authenticator is already set up")
    step = totp.check(row["secret"], body.code)
    if step is None:
        raise HTTPException(400, "That code doesn't match. Check the app shows SMM Shiro and try the newest code.")
    await db.execute("update admin_totp set confirmed_at = now(), last_step = :s where id = 1", {"s": step})
    codes = ["".join(secrets.choice(BACKUP_ALPHABET) for _ in range(4)) + "-" +
             "".join(secrets.choice(BACKUP_ALPHABET) for _ in range(4)) for _ in range(8)]
    await db.execute("delete from admin_backup_codes")
    for c in codes:
        await db.execute("insert into admin_backup_codes (code_hash) values (:h)", {"h": _backup_hash(c)})
    _session(response, mfa=True)
    log.info("admin authenticator set up")
    return {"backup_codes": codes}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/admin")
    return {"ok": True}


@router.get("/me", dependencies=[Depends(require_admin)])
async def me(db: DB = Depends(get_db)):
    row = await _totp_row(db)
    enrolled = bool(row and row["confirmed_at"])
    backups = await db.fetch_val("select count(*) from admin_backup_codes where used_at is null") if enrolled else 0
    return {"ok": True, "setup_required": not enrolled, "backup_codes_left": backups}


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
          (select count(*) from orders where status = 'needs_review') as needs_review,
          (select count(*) from orders where cancel_manual and cancel_requested_at is not null and cancel_declined_at is null
             and status in ('pending', 'in_progress', 'needs_review')) as cancels_pending
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
    rows = await db.fetch_all(f"""
        select u.id, u.email, u.created_at, u.tier_max,
               coalesce((select sum(delta) from ledger l where l.user_id = u.id), 0) as balance_php,
               (select count(*) from orders o where o.user_id = u.id) as orders,
               coalesce((select sum(amount_php) from topups t where t.user_id = u.id and t.status = 'credited'), 0) as topped_up_php,
               coalesce((select sum(o.price_php - coalesce((select sum(l.delta) from ledger l
                                                             where l.reason = 'refund' and l.ref = o.id::text), 0))
                           from orders o where o.user_id = u.id and o.source = 'web'), 0) as spent_php
          from users u where {where}
         order by u.created_at desc limit :lim offset :off
    """, params)
    # tier = the higher of what the spending earns now and what was reached before (tiers are kept forever)
    return [{**r, "tier": tiers.higher(tiers.tier_for_spent(float(r["spent_php"]))["name"], r["tier_max"])} for r in rows]


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


CANCEL_OPEN = """o.cancel_manual and o.cancel_requested_at is not null and o.cancel_declined_at is null
                 and o.status in ('pending', 'in_progress', 'needs_review')"""


@router.get("/cancellations", dependencies=[Depends(require_admin)])
async def cancellations(view: str = "open", db: DB = Depends(get_db)):
    """Cancel requests to take to the provider's support by hand. view=done: recent outcomes."""
    where = CANCEL_OPEN if view != "done" else """o.cancel_manual and o.cancel_requested_at is not null
        and not (""" + CANCEL_OPEN + ")"
    order = "o.cancel_requested_at asc" if view != "done" else "coalesce(o.cancel_declined_at, o.updated_at) desc"
    return await db.fetch_all(f"""
        select o.id, o.created_at, o.cancel_requested_at, o.cancel_contacted_at, o.cancel_declined_at, o.status,
               o.quantity, o.remains, o.price_php, o.link, o.provider_order_id, u.email, s.name as service_name, s.tier,
               coalesce((select sum(l.delta) from ledger l where l.reason = 'refund' and l.ref = o.id::text), 0) as refunded_php
          from orders o join users u on u.id = o.user_id join services s on s.id = o.service_id
         where {where}
         order by {order} limit 100
    """)


class CancelActionIn(BaseModel):
    action: str = Field(pattern="^(contacted|uncontacted|declined)$")


@router.post("/cancellations/{order_id}", dependencies=[Depends(require_admin)])
async def cancellation_action(order_id: int, body: CancelActionIn, db: DB = Depends(get_db)):
    """contacted: you asked the provider's support · declined: it couldn't be canceled (the customer sees that)."""
    sets = {"contacted": "cancel_contacted_at = now()", "uncontacted": "cancel_contacted_at = null",
            "declined": "cancel_declined_at = now()"}[body.action]
    row = await db.fetch_one(f"update orders o set {sets}, updated_at = now() where o.id = :id and {CANCEL_OPEN} returning o.id",
                             {"id": order_id})
    if not row:
        raise HTTPException(404, "Not in the cancellation list anymore")
    log.info("admin cancel request %s on order %s", body.action, order_id)
    return {"ok": True}


class SendingIn(BaseModel):
    paused: bool


@router.get("/sending", dependencies=[Depends(require_admin)])
async def sending_state(db: DB = Depends(get_db)):
    return {**await order_queue.state(db), "draining": orders_router._drain_lock.locked()}


@router.post("/sending", dependencies=[Depends(require_admin)])
async def set_sending(body: SendingIn):
    """Pause or resume sending orders to the provider. Resuming sends the backlog, one per second."""
    async with transaction() as db:   # saved before the backlog starts, so the sender sees it
        await order_queue.set_paused(db, body.paused)
    log.warning("admin %s sending orders to the provider", "paused" if body.paused else "resumed")
    if not body.paused:
        orders_router.kick_queue()
    async with transaction() as db:
        return {**await order_queue.state(db), "draining": orders_router._drain_lock.locked()}


@router.post("/sending/kick", dependencies=[Depends(require_admin)])
async def kick_sending():
    orders_router.kick_queue()
    return {"ok": True}


class AnnouncementIn(BaseModel):
    text: str = Field(default="", max_length=announcement.MAX_CHARS)


@router.get("/announcement", dependencies=[Depends(require_admin)])
async def get_announcement(db: DB = Depends(get_db)):
    return {**await announcement.read(db), "max_chars": announcement.MAX_CHARS}


@router.put("/announcement", dependencies=[Depends(require_admin)])
async def set_announcement(body: AnnouncementIn, db: DB = Depends(get_db)):
    """Empty text removes the bar."""
    return {**await announcement.save(db, body.text), "max_chars": announcement.MAX_CHARS}


@router.get("/growth", dependencies=[Depends(require_admin)])
async def growth(days: int = 14, db: DB = Depends(get_db)):
    """Day by day (Philippine time): sign-ups and how many of them went on to order and to pay,
    plus the day's paid top-ups and orders. And the all-time funnel."""
    days = max(1, min(days, 90))
    rows = await db.fetch_all("""
        with d as (select CAST(g AS date) as day
                     from generate_series(CAST(CAST(now() at time zone 'Asia/Manila' AS date) - CAST(:back AS int) AS timestamp),
                                          CAST(CAST(now() at time zone 'Asia/Manila' AS date) AS timestamp), interval '1 day') g),
             u as (select id, CAST(created_at at time zone 'Asia/Manila' AS date) as day,
                          exists (select 1 from orders o where o.user_id = users.id) as ordered,
                          exists (select 1 from topups t where t.user_id = users.id and t.status = 'credited') as paid
                     from users)
        select d.day,
               (select count(*) from u where u.day = d.day) as signups,
               (select count(*) from u where u.day = d.day and u.ordered) as signups_ordered,
               (select count(*) from u where u.day = d.day and u.paid) as signups_paid,
               (select count(*) from topups t where t.status = 'credited'
                   and CAST(t.credited_at at time zone 'Asia/Manila' AS date) = d.day) as topups,
               (select coalesce(sum(amount_php), 0) from topups t where t.status = 'credited'
                   and CAST(t.credited_at at time zone 'Asia/Manila' AS date) = d.day) as topups_php,
               (select count(*) from orders o where o.status not in ('failed', 'creating')
                   and CAST(o.created_at at time zone 'Asia/Manila' AS date) = d.day) as orders,
               (select count(distinct o.user_id) from orders o where o.status not in ('failed', 'creating')
                   and CAST(o.created_at at time zone 'Asia/Manila' AS date) = d.day) as buyers
          from d order by d.day desc
    """, {"back": days - 1})
    funnel = await db.fetch_one("""
        select count(*) as signed_up,
               count(*) filter (where exists (select 1 from orders o where o.user_id = u.id)) as ordered,
               count(*) filter (where exists (select 1 from orders o where o.user_id = u.id and o.source = 'trial')) as used_trial,
               count(*) filter (where exists (select 1 from topups t where t.user_id = u.id)) as tried_topup,
               count(*) filter (where exists (select 1 from topups t where t.user_id = u.id and t.status = 'credited')) as paid,
               count(*) filter (where (select count(*) from topups t where t.user_id = u.id and t.status = 'credited') >= 2) as paid_twice
          from users u
    """)
    return {"days": [{**r, "day": r["day"].isoformat(), "topups_php": float(r["topups_php"])} for r in rows],
            "funnel": dict(funnel)}


@router.get("/affiliates", dependencies=[Depends(require_admin)])
async def affiliates(db: DB = Depends(get_db)):
    """Customers who brought in at least one sign-up with their referral link, and who they brought."""
    referrals = await db.fetch_all("""
        select u.id, u.email, u.created_at, u.referred_by,
               (select count(*) from orders o where o.user_id = u.id) as orders,
               coalesce((select sum(amount_php) from topups t where t.user_id = u.id and t.status = 'credited'), 0) as topped_up_php,
               coalesce((select sum(l.delta) from ledger l join topups t on t.id::text = l.ref
                          where l.reason = 'referral' and t.user_id = u.id), 0) as commission_php
          from users u where u.referred_by is not null
         order by u.created_at desc
    """)
    refs = await db.fetch_all("""
        select r.id, r.email, r.ref_code, r.created_at,
               coalesce((select sum(delta) from ledger l where l.user_id = r.id and l.reason = 'referral'), 0) as earned_php,
               coalesce((select sum(delta) from ledger l where l.user_id = r.id), 0) as balance_php
          from users r where exists (select 1 from users u where u.referred_by = r.id)
    """)
    by_ref: dict[int, list] = {}
    for x in referrals:
        by_ref.setdefault(x["referred_by"], []).append({**x, "topped_up_php": float(x["topped_up_php"]),
                                                        "commission_php": float(x["commission_php"])})
    out = []
    for r in refs:
        people = by_ref.get(r["id"], [])
        out.append({**r, "earned_php": float(r["earned_php"]), "balance_php": float(r["balance_php"]),
                    "referred": len(people), "paying": sum(1 for p in people if p["topped_up_php"] > 0),
                    "referred_topups_php": round(sum(p["topped_up_php"] for p in people), 2),
                    "last_referral_at": max(p["created_at"] for p in people), "people": people})
    out.sort(key=lambda a: (-a["paying"], -a["referred"], -a["last_referral_at"].timestamp()))
    return {"affiliates": out, "referral_pct": get_settings().referral_pct}


@router.get("/trials", dependencies=[Depends(require_admin)])
async def trials(limit: int = 100, db: DB = Depends(get_db)):
    """Free trial orders, with the device and network they were claimed from. `shared_ip` counts
    other accounts whose trial came from the same IP (a sign of one person with several accounts)."""
    return await db.fetch_all("""
        select o.id, o.created_at, o.status, o.quantity, o.link, u.id as user_id, u.email, u.created_at as joined,
               u.trial_device, u.trial_ip,
               (select count(*) from users x where x.id <> u.id and x.trial_ip is not null and x.trial_ip = u.trial_ip) as shared_ip,
               exists (select 1 from topups t where t.user_id = u.id and t.status = 'credited') as paid_after
          from orders o join users u on u.id = o.user_id
         where o.source = 'trial'
         order by o.created_at desc limit :lim
    """, {"lim": max(1, min(limit, 200))})


@router.get("/ledger", dependencies=[Depends(require_admin)])
async def ledger(reason: str | None = None, q: str | None = None, limit: int = 100, db: DB = Depends(get_db)):
    """Every movement of customer money, newest first."""
    where, params = ["true"], {"lim": max(1, min(limit, 200))}
    if reason:
        where.append("l.reason = :r")
        params["r"] = reason
    if q:
        where.append("(u.email ilike :ql or u.id::text = :q or l.ref = :q)")
        params.update(q=q.strip().lstrip("#"), ql=f"%{q.strip()}%")
    rows = await db.fetch_all(f"""
        select l.id, l.created_at, l.delta, l.reason, l.ref, u.id as user_id, u.email
          from ledger l join users u on u.id = l.user_id
         where {' and '.join(where)}
         order by l.created_at desc, l.id desc limit :lim
    """, params)
    return [{**r, "delta": float(r["delta"])} for r in rows]


@router.get("/errors", dependencies=[Depends(require_admin)])
async def errors(db: DB = Depends(get_db)):
    """Problems customers' browsers reported, newest first."""
    return await db.fetch_all("""
        select id, created_at, page, message, user_agent from client_errors
         order by created_at desc limit 100
    """)
