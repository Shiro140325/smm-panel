"""Messenger bot conversation engine.

The bot sells only the owner's bot menu (name, price list, SMMGen service id), nothing from the
website catalog. The AI only reads the customer's messages and fills in a small form (what they want,
which menu item, quantity, link, language). Everything that matters is done here in code: the menu
prices, the next question, the summary, the payment link, placing the order, order status. The AI
can't invent a price or an item.

Cheap on tokens: a short instruction, only the last few messages, the menu item names, and replies
of one or two sentences.
"""
import asyncio
import json
import logging
import math
import re
import secrets
import time

from app.bot import llm, pricing
from app.config import get_settings
from app.db import transaction

log = logging.getLogger("bot")

HISTORY = 6            # past messages the AI sees
NOTES_MAX = 1500       # the owner's notes (FAQ, tone), in characters
LINK_RE = re.compile(r"https?://[^\s<>\"']+")
SETTING_KEYS = ("bot_model", "bot_notes", "bot_enabled")

# Fixed wording: (English, Tagalog/Taglish). Short and straight to the point.
T = {
    "help": ("Hi! Tell me what you need, e.g. \"1k TikTok likes\" + your link.",
             "Hi! Sabihin mo lang kailangan mo, hal. \"1k TikTok likes\" + link mo."),
    "menu": ("Here's what we have:\n{lines}\nWhich one? Reply with the number.",
             "Ito ang meron kami:\n{lines}\nAlin dito? Reply ng number."),
    "no_menu": ("Sorry, ordering isn't available right now.", "Pasensya, wala pang pwedeng i-order ngayon."),
    "ask_qty": ("How many? ({mn} to {mx})", "Ilan? ({mn} hanggang {mx})"),
    "bad_qty": ("{name}: {mn} to {mx} only. How many?", "{name}: {mn} hanggang {mx} lang. Ilan?"),
    "price": ("{name}: {list}.", "{name}: {list}."),
    "ask_comments": ("Now send your comments, one per line. Each line is 1 comment.",
                     "Send mo na yung comments, isa kada line. Bawat line = 1 comment."),
    "bad_comments": ("{name}: {mn} to {mx} comments only. Send them again, one per line.",
                     "{name}: {mn} hanggang {mx} comments lang. Send ulit, isa kada line."),
    "ask_link": ("Send the link to your post, video or profile.", "Send mo yung link ng post, video o profile mo."),
    "summary": ("{q} {name}\n{link}\nTotal: ₱{total}\nReply YES to get the payment link.",
                "{q} {name}\n{link}\nTotal: ₱{total}\nReply YES para sa payment link."),
    "pay": ("Pay ₱{amt} with GCash, Maya or your bank app. Your order starts once paid.",
            "Bayad ₱{amt} via GCash, Maya o bank app. Sisimulan namin pagkabayad."),
    "pay_extra": (" (₱{extra} extra stays as credit for next time.)", " (Yung ₱{extra} sobra, credit mo na sa susunod.)"),
    "test_pay": ("[Test mode] Payment link for ₱{amt} would be here. No order is placed.",
                 "[Test mode] Dito lalabas ang payment link (₱{amt}). Walang order na gagawin."),
    "placed": ("✅ Paid! Order #{id} is placed. Message \"status\" anytime.",
               "✅ Bayad na! Order #{id} placed na. Message mo lang \"status\" para i-check."),
    "placed_credit": ("✅ Order #{id} placed using your credit (₱{left} left).",
                      "✅ Order #{id} placed gamit credit mo (₱{left} natira)."),
    "place_failed": ("Your payment came in, but the order couldn't start: {err}. The ₱{amt} is saved as your credit.",
                     "Natanggap bayad mo pero di nag-start ang order: {err}. Naka-save ang ₱{amt} bilang credit mo."),
    "await_pay": ("Your payment link for ₱{amt} is above. Pay it to start the order, or say \"cancel\".",
                  "Nasa taas ang payment link (₱{amt}). Bayaran mo para mag-start, o sabihin \"cancel\"."),
    "cleared": ("Okay, cancelled. Anything else?", "Sige, cancel na. May iba pa?"),
    "no_orders": ("You have no orders yet.", "Wala ka pang order."),
    "status": ("Your orders:\n{lines}", "Mga order mo:\n{lines}"),
    "balance": ("Your credit: ₱{b}", "Credit mo: ₱{b}"),
    "cancel_which": ("Which order number?", "Anong order number?"),
    "spam": ("I can only help with orders for followers, likes, views and more. What do you need?",
             "Pang-order lang ako ng followers, likes, views at iba pa. Ano'ng kailangan mo?"),
    "error": ("Sorry, something went wrong. Try again in a moment.", "Pasensya, may error. Subukan ulit mamaya."),
}
STATUS_WORDS = {"queued": "Pending", "creating": "Processing", "pending": "Pending", "in_progress": "In progress",
                "completed": "Completed", "partial": "Partial", "canceled": "Canceled", "failed": "Failed (refunded)",
                "needs_review": "Being checked"}


def t(key: str, lang: str, **kw) -> str:
    en, tl = T[key]
    return (tl if lang == "tl" else en).format(**kw)


def money(x: float) -> str:
    return f"{x:,.2f}"


# ------------------------------------------------------------------ settings and menu

async def read_settings(db) -> dict:
    rows = await db.fetch_all("select key, value from site_settings where key = any(CAST(:k AS text[]))", {"k": list(SETTING_KEYS)})
    s = {r["key"]: r["value"] for r in rows}
    return {"model": s.get("bot_model") or llm.DEFAULT_MODEL, "notes": s.get("bot_notes") or "",
            "enabled": s.get("bot_enabled") == "1"}


async def save_settings(db, model: str | None = None, notes: str | None = None, enabled: bool | None = None) -> dict:
    for key, val in (("bot_model", model), ("bot_notes", notes[:NOTES_MAX] if notes is not None else None),
                     ("bot_enabled", None if enabled is None else ("1" if enabled else "0"))):
        if val is None:
            continue
        await db.execute("""insert into site_settings (key, value) values (:k, :v)
                            on conflict (key) do update set value = excluded.value, updated_at = now()""",
                         {"k": key, "v": val.strip() if key != "bot_enabled" else val})
    return await read_settings(db)


async def menu(db) -> list[dict]:
    """The bot menu: active items with a price list whose SMMGen service exists."""
    rows = await db.fetch_all("""
        select m.id, m.name, m.prices, ps.min_qty as min, ps.max_qty as max,
               lower(coalesce(ps.type, '')) = 'custom comments' as custom
          from bot_menu m
          join provider_services ps on ps.provider_service_id = m.provider_service_id
          join providers p on p.id = ps.provider_id and p.active
         where m.active
         order by m.sort, m.id
    """)
    seen, out = set(), []
    for r in rows:   # one row per item even if two providers list the same id
        if r["id"] not in seen:
            seen.add(r["id"])
            prices = r["prices"] if not isinstance(r["prices"], str) else json.loads(r["prices"])
            if pricing.clean(prices):
                out.append({**dict(r), "prices": prices})
    return out


def menu_text(items: list[dict]) -> str:
    """For the AI: number and name only (no prices, so it can't quote one)."""
    return "\n".join(f"{i}: {m['name']}" for i, m in enumerate(items, 1))


def menu_lines(items: list[dict], lang: str) -> str:
    return "\n".join(f"{i}) {m['name']} · {pricing.price_list_text(m['prices'])}" for i, m in enumerate(items, 1))


def item_total(item: dict, qty: int) -> float:
    return pricing.price_for(item["prices"], qty)


CANCEL_WORDS = {"cancel", "no", "stop", "wag na", "huwag na", "hindi", "ayoko", "cancel na"}


def _comments_turn(items: list[dict], draft: dict, text: str, lang: str) -> str:
    """The customer's typed comments for a custom-comments item: taken as they wrote them, one per line."""
    item = next((m for m in items if m["id"] == draft.get("item_id")), None)
    if not item:
        draft.clear()
        return t("menu", lang, lines=menu_lines(items, lang)) if items else t("no_menu", lang)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not LINK_RE.fullmatch(ln.strip())]
    if not item["min"] <= len(lines) <= item["max"]:
        return t("bad_comments", lang, name=item["name"], mn=f"{item['min']:,}", mx=f"{item['max']:,}")
    draft["comments"] = "\n".join(ln[:300] for ln in lines)
    return _next_step(items, draft, lang)


# ------------------------------------------------------------------ the turn

SYSTEM = """You read Messenger messages sent to SMM Shiro, a Philippine shop selling social media followers, likes, views and more.
Answer ONLY with JSON: {{"intent":"","lang":"en","item":0,"quantity":0,"order_id":0,"reply":""}}
intent: order (wants to buy) | price (asks how much) | menu (asks what's available) | choose (picks an item number) | yes (agrees/confirms) | no (declines or drops the current order) | status (asks about their orders) | cancel_order (wants a placed order canceled; set order_id) | balance (asks their credit) | question | greeting | spam (nonsense, random or unrelated messages).
item: the number of the matching menu item below, or 0 if unclear:
{menu}
quantity: a number (1k=1000). lang: "tl" if Tagalog/Taglish, else "en".
reply: only for question/greeting, one short friendly sentence in the customer's language (Taglish if they use it), never a price or a made-up fact; otherwise "".{notes}
Current draft: {draft}"""


async def _history(db, chat_id: int, before_id: int | None) -> list[dict]:
    rows = await db.fetch_all("""select direction, text from bot_messages where chat_id = :c and processed
                                    and (CAST(:b AS bigint) is null or id < :b)
                                  order by id desc limit :n""", {"c": chat_id, "n": HISTORY, "b": before_id})
    return [{"role": "user" if r["direction"] == "in" else "assistant", "content": r["text"][:300]} for r in reversed(rows)]


async def respond(db, chat: dict, texts: list[str], test: bool = False, before_id: int | None = None) -> tuple[list[dict], dict]:
    """One bot turn for the customer's latest message(s) (saved from id `before_id` on, so they're left
    out of the history). Returns (outgoing [{text, button?}], debug)."""
    st = dict(chat["state"] or {})
    draft = dict(st.get("draft") or {})
    lang = st.get("lang") or "en"
    settings = await read_settings(db)
    items = await menu(db)
    joined = "\n".join(texts)
    if draft.get("step") == "comments":   # their comments, word for word: no AI needed
        if joined.strip().lower().rstrip(".!") in CANCEL_WORDS:
            return await _save(db, chat, st, {}, [{"text": t("cleared", lang)}], {"ai": {"intent": "no"}})
        reply = _comments_turn(items, draft, joined, lang)
        return await _save(db, chat, st, draft, [{"text": reply}], {"ai": {"intent": "comments"}})
    notes = f"\nOwner's instructions (follow them; also facts for questions): {settings['notes']}" if settings["notes"] else ""
    shown = {k: draft[k] for k in ("item_name", "quantity", "link") if draft.get(k)}
    system = SYSTEM.format(menu=menu_text(items) or "(empty)", notes=notes,
                           draft=json.dumps(shown, ensure_ascii=False) if shown else "none")
    history = await _history(db, chat["id"], before_id)
    usage = {}
    try:
        ai, usage = await llm.ask(settings["model"], system, history + [{"role": "user", "content": joined[:800]}])
    except llm.LLMError as e:
        log.warning("bot AI failed for chat %s: %s", chat["id"], e)
        return [{"text": t("error", lang)}], {"error": str(e)}
    debug = {"ai": ai, **usage}
    intent = str(ai.get("intent") or "").lower()
    if ai.get("lang") in ("en", "tl"):
        lang = st["lang"] = ai["lang"]

    # --- spam: twice in a row → stop replying (the owner can unmute in the control panel)
    if intent == "spam":
        st["spam"] = int(st.get("spam") or 0) + 1
        if st["spam"] >= 2:
            await db.execute("update bot_chats set muted_at = now(), mute_reason = :r, state = :s where id = :c",
                             {"r": joined[:200], "s": json.dumps({**st, "draft": draft}), "c": chat["id"]})
            return [], {**debug, "muted": True}
        return await _save(db, chat, st, draft, [{"text": t("spam", lang)}], debug)
    st["spam"] = 0

    out: list[dict] = []
    if intent == "no":
        draft = {}
        out.append({"text": t("cleared", lang)})
    elif intent == "status":
        out.append({"text": await _status(db, chat, lang)})
    elif intent == "balance":
        out.append({"text": t("balance", lang, b=money(await _credit(db, chat)))})
    elif intent == "cancel_order":
        out.append({"text": await _cancel(db, chat, ai, lang)})
    elif intent == "yes" and draft.get("step") == "confirm":
        out.extend(await _checkout(db, chat, draft, lang, test))
    elif intent == "menu":
        out.append({"text": t("menu", lang, lines=menu_lines(items, lang)) if items else t("no_menu", lang)})
        if items:
            draft["step"] = "choose"
    elif intent in ("question", "greeting") and not _has_order_info(ai, joined):
        if draft.get("step") == "await_payment":
            out.append({"text": t("await_pay", lang, amt=money(draft.get("pay_amount") or 0))})
        else:
            out.append({"text": str(ai.get("reply") or "").strip()[:300] or t("help", lang)})
    else:   # order / price / choose / yes (outside a confirm) / anything carrying order details
        _merge(items, draft, ai, joined)
        out.append({"text": _next_step(items, draft, lang, price_only=intent == "price")})
    return await _save(db, chat, st, draft, out, debug)


def _has_order_info(ai: dict, text: str) -> bool:
    return bool(ai.get("item") or LINK_RE.search(text))


def _merge(items: list[dict], draft: dict, ai: dict, text: str) -> None:
    if draft.get("step") == "await_payment":   # a new request replaces an unpaid one
        draft.clear()
    pick = ai.get("item")
    if not pick and draft.get("step") == "choose" and text.strip().isdigit():
        pick = int(text.strip())
    try:
        pick = int(pick or 0)
    except (TypeError, ValueError):
        pick = 0
    if 1 <= pick <= len(items) and items[pick - 1]["id"] != draft.get("item_id"):
        draft.update(item_id=items[pick - 1]["id"], item_name=items[pick - 1]["name"])
        draft.pop("comments", None)
    try:
        q = int(float(ai.get("quantity") or 0))
    except (TypeError, ValueError):
        q = 0
    if q <= 0 and draft.get("item_id") and draft.get("step") == "collect" and text.strip().replace(",", "").isdigit():
        q = int(text.strip().replace(",", ""))   # a bare number answering "how many?"
    if q > 0:
        draft["quantity"] = q
    m = LINK_RE.search(text)   # the link from the customer's own words, never retyped by the AI
    if m:
        draft["link"] = m.group(0).rstrip(".,)")


def _next_step(items: list[dict], draft: dict, lang: str, price_only: bool = False) -> str:
    """Ask for the next missing piece, or show the summary."""
    if not items:
        draft.clear()
        return t("no_menu", lang)
    item = next((m for m in items if m["id"] == draft.get("item_id")), None)
    if not item:
        draft.pop("item_id", None)
        draft["step"] = "choose"
        return t("menu", lang, lines=menu_lines(items, lang))
    draft["step"] = "collect"
    head = t("price", lang, name=item["name"], list=pricing.price_list_text(item["prices"])) + "\n" if price_only else ""
    if item["custom"]:   # typed comments: the link, then the comments; how many = how many lines
        draft.pop("quantity", None)
        if not draft.get("link"):
            return head + t("ask_link", lang)
        if not draft.get("comments"):
            draft["step"] = "comments"
            return head + t("ask_comments", lang)
        qty = len(draft["comments"].splitlines())
        total = item_total(item, qty)
        draft.update(step="confirm", total=total, quantity=qty)
        return t("summary", lang, q=f"{qty:,}", name=item["name"], link=draft["link"], total=money(total))
    qty = draft.get("quantity")
    if not qty:
        return head + t("ask_qty", lang, mn=f"{item['min']:,}", mx=f"{item['max']:,}")
    if not item["min"] <= qty <= item["max"]:
        draft.pop("quantity", None)
        return t("bad_qty", lang, name=item["name"], mn=f"{item['min']:,}", mx=f"{item['max']:,}")
    total = item_total(item, qty)
    if not draft.get("link"):
        if price_only:
            return f"{qty:,} {item['name']}: ₱{money(total)}. " + t("ask_link", lang)
        return t("ask_link", lang)
    draft.update(step="confirm", total=total)
    return t("summary", lang, q=f"{qty:,}", name=item["name"], link=draft["link"], total=money(total))


async def _credit(db, chat: dict) -> float:
    if not chat.get("user_id"):
        return 0.0
    return float(await db.fetch_val("select coalesce(sum(delta), 0) from ledger where user_id = :u", {"u": chat["user_id"]}))


async def _status(db, chat: dict, lang: str) -> str:
    if not chat.get("user_id"):
        return t("no_orders", lang)
    rows = await db.fetch_all("""select o.id, o.quantity, o.status, coalesce(o.label, s.name) as name from orders o
                                    join services s on s.id = o.service_id
                                  where o.user_id = :u order by o.id desc limit 5""", {"u": chat["user_id"]})
    if not rows:
        return t("no_orders", lang)
    return t("status", lang, lines="\n".join(f"#{r['id']} {r['name']} ×{r['quantity']:,}: {STATUS_WORDS.get(r['status'], r['status'])}" for r in rows))


async def _cancel(db, chat: dict, ai: dict, lang: str) -> str:
    try:
        oid = int(ai.get("order_id") or 0)
    except (TypeError, ValueError):
        oid = 0
    if not oid or not chat.get("user_id"):
        return t("cancel_which", lang)
    from fastapi import HTTPException
    from app.routers.orders import cancel_order
    try:
        return await cancel_order(oid, chat["user_id"])
    except HTTPException as e:
        return str(e.detail)


async def _checkout(db, chat: dict, draft: dict, lang: str, test: bool) -> list[dict]:
    """The customer said YES to the summary: pay from credit, or a payment link for the difference."""
    total = float(draft.get("total") or 0)
    order = {"item_id": draft["item_id"], "link": draft["link"], "quantity": draft["quantity"]}
    if draft.get("comments"):
        order["comments"] = draft["comments"]
    if test:
        draft.clear()
        return [{"text": t("test_pay", lang, amt=money(total))}]
    credit = await _credit(db, chat)
    if credit >= total:
        res = await _place(chat["user_id"], order)
        draft.clear()
        if res.get("error"):
            return [{"text": res["error"]}]
        left = await _credit(db, chat)
        return [{"text": t("placed_credit", lang, id=res["id"], left=money(left))}]
    s = get_settings()
    amount = max(math.ceil(total - credit), s.bot_min_payment_php)
    from app.payments import create_checkout
    try:
        topup_id, url = await create_checkout(chat["user_id"], amount, description="SMM Shiro order",
                                              bot_chat_id=chat["id"], bot_order=order)
    except Exception as e:
        log.warning("bot checkout failed: %s", e)
        return [{"text": t("error", lang)}]
    draft.update(step="await_payment", pay_amount=amount, topup_id=topup_id)
    extra = round(amount - (total - credit), 2)
    text = t("pay", lang, amt=money(amount)) + (t("pay_extra", lang, extra=money(extra)) if extra >= 1 else "")
    return [{"text": text, "button": {"title": f"Pay ₱{money(amount)}", "url": url}}]


async def _place(user_id: int, order: dict) -> dict:
    from fastapi import HTTPException
    from app.routers.orders import place_chat_order
    try:
        return await place_chat_order(user_id, order["item_id"], order["link"], order["quantity"], order.get("comments"))
    except HTTPException as e:
        return {"error": str(e.detail)}


async def _save(db, chat: dict, st: dict, draft: dict, out: list[dict], debug: dict) -> tuple[list[dict], dict]:
    st["draft"] = draft
    await db.execute("update bot_chats set state = :s, last_message_at = now() where id = :c",
                     {"s": json.dumps(st, ensure_ascii=False), "c": chat["id"]})
    chat["state"] = st
    return out, debug


# ------------------------------------------------------------------ chats, messages, delivery

async def get_chat(db, channel: str, external_id: str, name: str | None = None) -> dict:
    row = await db.fetch_one("""insert into bot_chats (channel, external_id, name) values (:ch, :x, :n)
                                on conflict (channel, external_id) do update set name = coalesce(excluded.name, bot_chats.name)
                                returning *""", {"ch": channel, "x": external_id, "n": name})
    chat = dict(row)
    if chat["state"] and isinstance(chat["state"], str):
        chat["state"] = json.loads(chat["state"])
    if channel == "messenger" and not chat["user_id"]:   # a chat-only customer account
        from app.security import hash_password
        uid = await db.fetch_val("""insert into users (email, password_hash, email_verified_at, channel)
                                    values (:e, :h, now(), 'messenger') returning id""",
                                 {"e": f"messenger-{external_id}@chat.smmshiro.invalid", "h": hash_password(secrets.token_hex(16))})
        await db.execute("update bot_chats set user_id = :u where id = :c", {"u": uid, "c": chat["id"]})
        chat["user_id"] = uid
    return chat


async def store_out(db, chat_id: int, out: list[dict], debug: dict | None = None) -> None:
    for i, m in enumerate(out):
        text = m["text"] + (f"\n[{m['button']['title']}] {m['button']['url']}" if m.get("button") else "")
        d = debug if i == 0 else None
        await db.execute("""insert into bot_messages (chat_id, direction, text, tokens_in, tokens_out, cost_usd, debug)
                            values (:c, 'out', :t, :ti, :to, :cost, :d)""",
                         {"c": chat_id, "t": text, "ti": (d or {}).get("tokens_in"), "to": (d or {}).get("tokens_out"),
                          "cost": (d or {}).get("cost_usd"), "d": json.dumps(d, ensure_ascii=False, default=str) if d else None})


async def deliver(chat: dict, out: list[dict]) -> None:
    if chat["channel"] == "messenger" and out:
        from app.bot import messenger
        for m in out:
            await messenger.send(chat["external_id"], m)


_locks: dict[int, asyncio.Lock] = {}


async def process_chat(chat_id: int) -> None:
    """Answer everything this customer sent since the last reply, as one turn."""
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        async with transaction() as db:
            # "no key update": the payment row created mid-turn references this chat (a foreign key needs a
            # share lock on it), so a plain "for update" here would make that insert wait on us forever
            chat = await db.fetch_one("select * from bot_chats where id = :c for no key update", {"c": chat_id})
            if not chat:
                return
            chat = dict(chat)
            if isinstance(chat["state"], str):
                chat["state"] = json.loads(chat["state"])
            rows = await db.fetch_all("""update bot_messages set processed = true
                                          where chat_id = :c and direction = 'in' and not processed returning id, text""",
                                      {"c": chat_id})
            if not rows or chat["muted_at"]:
                return
            texts = [r["text"] for r in sorted(rows, key=lambda r: r["id"])]
            if chat["channel"] == "messenger":
                from app.bot import messenger
                asyncio.get_running_loop().create_task(messenger.typing(chat["external_id"]))
            out, debug = await respond(db, chat, texts, before_id=min(r["id"] for r in rows))
            await store_out(db, chat_id, out, debug)
        await deliver(chat, out)


async def after_payment(topup_id: str) -> None:
    """A chat payment was credited: place the order it was for and tell the customer."""
    async with transaction() as db:
        # take the order off the payment in the same step that reads it, so it's placed once
        row = await db.fetch_one("""with old as (select id, bot_chat_id, bot_order, user_id, amount_php from topups
                                                 where id = CAST(:id AS uuid) and bot_order is not null
                                                   and bot_chat_id is not null for update)
                                    update topups t set bot_order = null from old where t.id = old.id
                                    returning old.bot_chat_id, old.bot_order, old.user_id, old.amount_php""", {"id": topup_id})
    if not row:
        return
    order = row["bot_order"] if isinstance(row["bot_order"], dict) else json.loads(row["bot_order"])
    res = await _place(row["user_id"], order)
    async with transaction() as db:
        chat = dict(await db.fetch_one("select * from bot_chats where id = :c", {"c": row["bot_chat_id"]}))
        st = chat["state"] if isinstance(chat["state"], dict) else json.loads(chat["state"] or "{}")
        lang = st.get("lang") or "en"
        out = [{"text": t("place_failed", lang, err=res["error"], amt=money(row["amount_php"])) if res.get("error")
                else t("placed", lang, id=res["id"])}]
        st["draft"] = {}
        await db.execute("update bot_chats set state = :s, last_message_at = now() where id = :c",
                         {"s": json.dumps(st, ensure_ascii=False), "c": chat["id"]})
        await store_out(db, chat["id"], out)
    await deliver(chat, out)
