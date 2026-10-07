"""Facebook Messenger: webhook in, Send API out.

Messages are saved as they arrive; the bot answers once the customer pauses (BOT_DEBOUNCE_SECONDS after
their last message), so several quick messages get one reply. Off until META_* and OPENROUTER_API_KEY
are set and the bot is switched on in the control panel.
"""
import asyncio
import hashlib
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.bot import engine
from app.config import get_settings
from app.db import transaction

router = APIRouter(prefix="/messenger", tags=["messenger"])
log = logging.getLogger("bot.messenger")
_timers: dict[int, asyncio.Task] = {}


def configured() -> bool:
    s = get_settings()
    return bool(s.meta_page_token and s.meta_app_secret and s.meta_verify_token and s.openrouter_api_key)


@router.get("/webhook")
async def verify(request: Request):
    """Meta's one-time check when the webhook URL is saved in the app."""
    q = request.query_params
    s = get_settings()
    if q.get("hub.mode") == "subscribe" and s.meta_verify_token and hmac.compare_digest(q.get("hub.verify_token") or "", s.meta_verify_token):
        return PlainTextResponse(q.get("hub.challenge") or "")
    raise HTTPException(403, "Verification failed")


@router.post("/webhook")
async def receive(request: Request):
    raw = await request.body()
    s = get_settings()
    sig = request.headers.get("x-hub-signature-256", "")
    good = "sha256=" + hmac.new(s.meta_app_secret.encode(), raw, hashlib.sha256).hexdigest() if s.meta_app_secret else ""
    if not good or not hmac.compare_digest(sig, good):
        raise HTTPException(403, "Bad signature")
    body = json.loads(raw or b"{}")
    async with transaction() as db:
        enabled = (await engine.read_settings(db))["enabled"]
        for entry in body.get("entry", []):
            for ev in entry.get("messaging", []):
                psid = (ev.get("sender") or {}).get("id")
                msg = ev.get("message") or {}
                if not psid or msg.get("is_echo"):
                    continue
                text = (msg.get("text") or "").strip()
                if not text and msg.get("attachments"):
                    text = "[sent an attachment]"
                if not text and ev.get("postback"):
                    text = ev["postback"].get("title") or ev["postback"].get("payload") or ""
                if not text:
                    continue
                chat = await engine.get_chat(db, "messenger", psid)
                await db.execute("""insert into bot_messages (chat_id, direction, text, processed)
                                    values (:c, 'in', :t, :p)""", {"c": chat["id"], "t": text[:2000], "p": not enabled})
                await db.execute("update bot_chats set last_message_at = now() where id = :c", {"c": chat["id"]})
                if enabled:
                    _schedule(chat["id"])
    return {"ok": True}


def _schedule(chat_id: int) -> None:
    """(Re)start the wait: the reply goes out once the customer stops typing for a few seconds."""
    old = _timers.pop(chat_id, None)
    if old and not old.done():
        old.cancel()
    _timers[chat_id] = asyncio.get_running_loop().create_task(_after_pause(chat_id))


async def _after_pause(chat_id: int) -> None:
    try:
        await asyncio.sleep(get_settings().bot_debounce_seconds)
    except asyncio.CancelledError:
        return
    _timers.pop(chat_id, None)
    try:
        await engine.process_chat(chat_id)
    except Exception:
        log.exception("bot turn failed for chat %s", chat_id)


async def catch_up() -> None:
    """After a restart: answer chats whose messages arrived but were never answered."""
    async with transaction() as db:
        if not (await engine.read_settings(db))["enabled"]:
            return
        rows = await db.fetch_all("""select distinct m.chat_id from bot_messages m join bot_chats c on c.id = m.chat_id
                                      where not m.processed and c.channel = 'messenger' and c.muted_at is null
                                        and m.created_at < now() - interval '10 seconds'""")
    for r in rows:
        if r["chat_id"] not in _timers:
            _schedule(r["chat_id"])


async def _post(payload: dict) -> None:
    s = get_settings()
    if not s.meta_page_token:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(f"{s.meta_graph_url}/me/messages", params={"access_token": s.meta_page_token}, json=payload)
        if r.status_code >= 300:
            log.warning("Messenger send failed: %s %s", r.status_code, r.text[:300])
    except httpx.HTTPError as e:
        log.warning("Messenger send failed: %s", e)


async def typing(psid: str) -> None:
    await _post({"recipient": {"id": psid}, "sender_action": "typing_on"})


async def send(psid: str, m: dict) -> None:
    if m.get("button"):
        message = {"attachment": {"type": "template", "payload": {
            "template_type": "button", "text": m["text"][:640],
            "buttons": [{"type": "web_url", "url": m["button"]["url"], "title": m["button"]["title"][:20]}]}}}
    else:
        message = {"text": m["text"][:2000]}
    await _post({"recipient": {"id": psid}, "messaging_type": "RESPONSE", "message": message})
