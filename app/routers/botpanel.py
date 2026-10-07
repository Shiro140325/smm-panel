"""The Messenger bot's own control panel (botfb.smmshiro.com → /botfb/api/*), unlocked with a 6-digit PIN.

Three wrong PINs in a row lock the PIN: only the owner's smmshiro account password opens the panel then
(and resets the count). The PIN's hash, the count and the owner's user id live in the bot_panel row.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import fx
from app.bot import engine as bot_engine
from app.bot import pricing
from app.config import get_settings
from app.db import DB, get_db, transaction
from app.ratelimit import client_ip
from app.security import verify_password

router = APIRouter(prefix="/botfb/api", tags=["botfb"])
log = logging.getLogger("botfb")

COOKIE = "bot_session"
TTL_DAYS = 30
MAX_PIN_FAILS = 3


async def _panel(db, lock: bool = False):
    return await db.fetch_one("select pin_hash, pin_fails, owner_user_id, session_epoch from bot_panel where id = 1"
                              + (" for update" if lock else ""))


async def require_bot(request: Request, db: DB = Depends(get_db, scope="function")) -> None:
    try:
        payload = jwt.decode(request.cookies.get(COOKIE) or "", get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Enter your PIN")
    row = await _panel(db)
    if payload.get("botp") != 1 or not row or payload.get("e") != row["session_epoch"]:
        raise HTTPException(401, "Enter your PIN")


def _session(response: Response, epoch: int) -> None:
    s = get_settings()
    token = jwt.encode({"botp": 1, "e": epoch, "exp": datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)},
                       s.jwt_secret, algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=TTL_DAYS * 86400, path="/")


@router.get("/state")
async def state(db: DB = Depends(get_db, scope="function")):
    row = await _panel(db)
    return {"ready": bool(row and row["pin_hash"]), "needs_password": bool(row and row["pin_fails"] >= MAX_PIN_FAILS)}


class PinIn(BaseModel):
    pin: str = Field(max_length=12)


class PasswordIn(BaseModel):
    password: str = Field(max_length=128)


@router.post("/login")
async def login(body: PinIn, request: Request, response: Response):
    async with transaction() as db:
        row = await _panel(db, lock=True)
        if not row or not row["pin_hash"]:
            raise HTTPException(503, "The PIN isn't set up yet")
        if row["pin_fails"] >= MAX_PIN_FAILS:
            raise HTTPException(423, "Too many wrong PINs. Enter your account password.")
        pin = body.pin.strip()
        ok = len(pin) == 6 and pin.isdigit() and verify_password(pin, row["pin_hash"])
        fails = 0 if ok else row["pin_fails"] + 1
        await db.execute("update bot_panel set pin_fails = :f where id = 1", {"f": fails})
    if not ok:
        log.warning("bot panel: wrong PIN from %s (%d/%d)", client_ip(request), fails, MAX_PIN_FAILS)
        if fails >= MAX_PIN_FAILS:
            raise HTTPException(423, "Too many wrong PINs. Enter your account password.")
        left = MAX_PIN_FAILS - fails
        raise HTTPException(401, f"Wrong PIN. {left} {'try' if left == 1 else 'tries'} left.")
    _session(response, row["session_epoch"])
    return {"ok": True}


@router.post("/unlock")
async def unlock(body: PasswordIn, request: Request, response: Response):
    """After 3 wrong PINs: the owner's smmshiro account password opens the panel and resets the count."""
    from app.routers.auth import login_fails_email, login_fails_ip   # same lockout as logging in
    async with transaction() as db:
        row = await _panel(db, lock=True)
        if not row or not row["owner_user_id"]:
            raise HTTPException(503, "The panel's owner account isn't set")
        ip, key = client_ip(request), f"u:{row['owner_user_id']}"
        login_fails_ip.check(ip)
        login_fails_email.check(key)
        h = await db.fetch_val("select password_hash from users where id = :u", {"u": row["owner_user_id"]})
        if not h or not verify_password(body.password, h):
            login_fails_ip.add(ip)
            login_fails_email.add(key)
            log.warning("bot panel: wrong account password from %s", ip)
            raise HTTPException(401, "Wrong password")
        await db.execute("update bot_panel set pin_fails = 0 where id = 1")
    login_fails_email.reset(key)
    _session(response, row["session_epoch"])
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ------------------------------------------------------------------ Messenger bot

class BotSettingsIn(BaseModel):
    model: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=bot_engine.NOTES_MAX)
    enabled: bool | None = None


@router.get("/bot", dependencies=[Depends(require_bot)])
async def bot_overview(db: DB = Depends(get_db, scope="function")):
    s = get_settings()
    stats = await db.fetch_one("""
        select (select count(*) from bot_chats where channel = 'messenger') as chats,
               (select count(*) from bot_chats where channel = 'messenger' and muted_at is not null) as muted,
               (select count(*) from bot_messages m join bot_chats c on c.id = m.chat_id
                 where c.channel = 'messenger' and m.direction = 'out' and m.created_at > now() - interval '7 days') as replies_7d,
               (select coalesce(sum(cost_usd), 0) from bot_messages where created_at > now() - interval '7 days') as cost_7d,
               (select coalesce(sum(cost_usd), 0) from bot_messages) as cost_all,
               (select count(*) from orders where source = 'chat') as orders
    """)
    chats = await db.fetch_all("""
        select c.id, c.external_id, c.name, c.user_id, c.muted_at, c.mute_reason, c.last_message_at,
               (select text from bot_messages m where m.chat_id = c.id order by id desc limit 1) as last_text,
               (select count(*) from orders o where o.user_id = c.user_id) as orders
          from bot_chats c where c.channel = 'messenger'
         order by c.muted_at is null, c.last_message_at desc nulls last limit 50
    """)
    return {"settings": await bot_engine.read_settings(db),
            "setup": {"openrouter": bool(s.openrouter_api_key), "meta_token": bool(s.meta_page_token),
                      "meta_secret": bool(s.meta_app_secret), "verify_token": bool(s.meta_verify_token),
                      "webhook_url": f"{s.base_url}/messenger/webhook"},
            "stats": {**dict(stats), "cost_7d": float(stats["cost_7d"]), "cost_all": float(stats["cost_all"])},
            "usd_to_php": fx.usd_to_php_raw(),
            "chats": chats}


@router.put("/bot", dependencies=[Depends(require_bot)])
async def bot_save(body: BotSettingsIn, db: DB = Depends(get_db, scope="function")):
    return await bot_engine.save_settings(db, model=body.model, notes=body.notes, enabled=body.enabled)


class BotTestIn(BaseModel):
    session: str = Field(min_length=4, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    text: str = Field(min_length=1, max_length=800)


@router.post("/bot/test", dependencies=[Depends(require_bot)])
async def bot_test(body: BotTestIn, db: DB = Depends(get_db, scope="function")):
    """The owner chats as a customer. Same engine and AI; no payment link and no order."""
    if not get_settings().openrouter_api_key:
        raise HTTPException(400, "Add OPENROUTER_API_KEY first")
    chat = await bot_engine.get_chat(db, "playground", body.session)
    out, debug = await bot_engine.respond(db, chat, [body.text], test=True)
    await db.execute("insert into bot_messages (chat_id, direction, text) values (:c, 'in', :t)", {"c": chat["id"], "t": body.text})
    await bot_engine.store_out(db, chat["id"], out, debug)
    return {"replies": out, "debug": debug,
            "draft": (chat.get("state") or {}).get("draft") or {}}


@router.post("/bot/test/reset", dependencies=[Depends(require_bot)])
async def bot_test_reset(body: dict, db: DB = Depends(get_db, scope="function")):
    await db.execute("delete from bot_chats where channel = 'playground' and external_id = :x", {"x": str(body.get("session", ""))[:40]})
    return {"ok": True}


@router.get("/bot/chats/{chat_id}", dependencies=[Depends(require_bot)])
async def bot_chat(chat_id: int, db: DB = Depends(get_db, scope="function")):
    chat = await db.fetch_one("select id, name, external_id, muted_at, mute_reason, user_id from bot_chats where id = :c", {"c": chat_id})
    if not chat:
        raise HTTPException(404, "Chat not found")
    msgs = await db.fetch_all("""select id, direction, text, created_at, cost_usd from bot_messages
                                  where chat_id = :c order by id desc limit 100""", {"c": chat_id})
    return {"chat": chat, "messages": list(reversed(msgs))}


async def _provider_service(db, psid: int):
    return await db.fetch_one("""
        select ps.provider_service_id, ps.name, ps.category, ps.type, ps.rate, ps.min_qty, ps.max_qty, ps.refill, ps.cancel,
               p.currency, p.name as provider
          from provider_services ps join providers p on p.id = ps.provider_id and p.active
         where ps.provider_service_id = :id order by p.id limit 1""", {"id": psid})


def _cost_1k(svc) -> float:
    """What SMMGen charges us for 1,000 of this service, in pesos (live rate, no buffer)."""
    rate = fx.usd_to_php_raw() if (svc["currency"] or "USD").upper() == "USD" else 1.0
    return round(float(svc["rate"]) * rate, 4)


def _is_custom(svc) -> bool:
    return (svc["type"] or "").strip().lower() == "custom comments"


@router.get("/bot/lookup", dependencies=[Depends(require_bot)])
async def bot_lookup(id: int, db: DB = Depends(get_db, scope="function")):
    """SMMGen's details for a service id, to fill in a menu item."""
    svc = await _provider_service(db, id)
    if not svc:
        raise HTTPException(404, f"SMMGen has no service #{id} (or it isn't in the synced catalog yet)")
    return {**dict(svc), "rate": float(svc["rate"]), "cost_1k_php": _cost_1k(svc), "custom_comments": _is_custom(svc)}


class PriceIn(BaseModel):
    qty: int = Field(gt=0, le=100_000_000)
    price: float = Field(gt=0, le=1_000_000)


class MenuItemIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    provider_service_id: int = Field(gt=0)
    prices: list[PriceIn] = Field(min_length=1, max_length=20)
    active: bool = True
    sort: int = 0


def _prices(raw) -> list:
    return json.loads(raw) if isinstance(raw, str) else (raw or [])


@router.get("/bot/menu", dependencies=[Depends(require_bot)])
async def bot_menu(db: DB = Depends(get_db, scope="function")):
    rows = await db.fetch_all("select * from bot_menu order by sort, id")
    out = []
    for r in rows:
        svc = await _provider_service(db, r["provider_service_id"])
        out.append({**dict(r), "prices": [{"qty": q, "price": p} for q, p in pricing.clean(_prices(r["prices"]))],
                    "smmgen": {"name": svc["name"], "min": svc["min_qty"], "max": svc["max_qty"], "cost_1k_php": _cost_1k(svc),
                               "custom_comments": _is_custom(svc)} if svc else None})
    return out


async def _check_menu_item(db, body: MenuItemIn) -> str:
    svc = await _provider_service(db, body.provider_service_id)
    if not svc:
        raise HTTPException(400, f"SMMGen has no service #{body.provider_service_id}")
    qtys = [p.qty for p in body.prices]
    if len(set(qtys)) != len(qtys):
        raise HTTPException(400, "Each amount can be in the price list once")
    for q in qtys:
        if not svc["min_qty"] <= q <= svc["max_qty"]:
            raise HTTPException(400, f"{q:,} is outside what SMMGen #{body.provider_service_id} takes "
                                     f"({svc['min_qty']:,} to {svc['max_qty']:,})")
    return json.dumps(sorted([[p.qty, round(p.price, 2)] for p in body.prices]))


@router.post("/bot/menu", dependencies=[Depends(require_bot)])
async def bot_menu_add(body: MenuItemIn, db: DB = Depends(get_db, scope="function")):
    prices = await _check_menu_item(db, body)
    row = await db.fetch_one("""insert into bot_menu (name, prices, provider_service_id, active, sort)
                                values (:n, CAST(:pr AS jsonb), :ps, :a, :s) returning id""",
                             {"n": body.name.strip(), "pr": prices, "ps": body.provider_service_id, "a": body.active, "s": body.sort})
    return {"id": row["id"]}


@router.put("/bot/menu/{item_id}", dependencies=[Depends(require_bot)])
async def bot_menu_edit(item_id: int, body: MenuItemIn, db: DB = Depends(get_db, scope="function")):
    prices = await _check_menu_item(db, body)
    row = await db.fetch_one("""update bot_menu set name = :n, prices = CAST(:pr AS jsonb), provider_service_id = :ps,
                                       active = :a, sort = :s where id = :id returning id""",
                             {"id": item_id, "n": body.name.strip(), "pr": prices, "ps": body.provider_service_id,
                              "a": body.active, "s": body.sort})
    if not row:
        raise HTTPException(404, "Menu item not found")
    return {"ok": True}


@router.delete("/bot/menu/{item_id}", dependencies=[Depends(require_bot)])
async def bot_menu_delete(item_id: int, db: DB = Depends(get_db, scope="function")):
    await db.execute("delete from bot_menu where id = :id", {"id": item_id})
    return {"ok": True}


class AiIn(BaseModel):
    on: bool


@router.post("/bot/chats/{chat_id}/ai", dependencies=[Depends(require_bot)])
async def bot_chat_ai(chat_id: int, body: AiIn, db: DB = Depends(get_db, scope="function")):
    """The owner turns the AI off (or back on) for one customer. While off, their messages are saved, not answered."""
    if body.on:
        return await bot_unmute(chat_id, db)
    row = await db.fetch_one("update bot_chats set muted_at = now(), mute_reason = 'owner' where id = :c returning id", {"c": chat_id})
    if not row:
        raise HTTPException(404, "Chat not found")
    return {"ok": True}


@router.post("/bot/chats/{chat_id}/unmute", dependencies=[Depends(require_bot)])
async def bot_unmute(chat_id: int, db: DB = Depends(get_db, scope="function")):
    row = await db.fetch_one("""update bot_chats set muted_at = null, mute_reason = null,
                                       state = jsonb_set(coalesce(state, '{}'::jsonb), '{spam}', '0')
                                 where id = :c returning id""", {"c": chat_id})
    if not row:
        raise HTTPException(404, "Chat not found")
    return {"ok": True}
