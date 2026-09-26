"""Reseller API, the standard SMM panel API v2: POST /api/v2 with key + action.

Accepts form fields (what panel scripts send) or JSON; POST only, so keys stay out of URLs and logs. Errors come back as
{"error": "..."} with HTTP 200, like other panels, so existing reseller scripts work unchanged.
Money is in PHP.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.db import transaction
from app.ratelimit import Limiter, client_ip
from app.routers.account import hash_api_key
from app.routers.orders import cancel_order, place_order, refill_order
from app.routers.services import _rows
from app.security import balance_of

router = APIRouter(tags=["reseller api"])
log = logging.getLogger("api")

per_key = Limiter(120, 60, "Too many requests: up to 120 per minute")
bad_keys = Limiter(30, 15 * 60, "Too many invalid keys. Try again in 15 minutes.")
MAX_IDS = 100

STATUS = {"creating": "Pending", "pending": "Pending", "in_progress": "In progress", "completed": "Completed",
          "partial": "Partial", "canceled": "Canceled", "failed": "Canceled", "needs_review": "Processing"}
PLATFORMS = {"tiktok": "TikTok", "facebook": "Facebook", "instagram": "Instagram", "youtube": "YouTube", "x": "X",
             "telegram": "Telegram", "whatsapp": "WhatsApp", "spotify": "Spotify", "threads": "Threads",
             "shopee": "Shopee", "linkedin": "LinkedIn", "kick": "Kick", "twitch": "Twitch", "snapchat": "Snapchat",
             "soundcloud": "SoundCloud", "discord": "Discord", "reddit": "Reddit", "pinterest": "Pinterest",
             "other": "Other"}
REFILL_STATUS = {"pending": "Pending", "completed": "Completed", "rejected": "Rejected", "ignored": "Rejected"}


class ApiError(Exception):
    pass


def _ids(raw, name: str) -> list[int]:
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        raise ApiError(f"Missing {name}")
    if len(parts) > MAX_IDS:
        raise ApiError(f"Up to {MAX_IDS} IDs per request")
    try:
        return [int(p) for p in parts]
    except ValueError:
        raise ApiError(f"Incorrect {name}")


def _int(raw, name: str) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise ApiError(f"Incorrect {name}")


async def _params(request: Request) -> dict:
    data = {}
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            data.update({k: v for k, v in body.items() if v is not None})
    elif "form" in ctype:
        data.update(dict(await request.form()))
    return data


async def _user_for(key: str, request: Request) -> int:
    ip = client_ip(request)
    bad_keys.check(ip)
    async with transaction() as db:
        uid = await db.fetch_val("select id from users where api_key_hash = :h", {"h": hash_api_key(key)}) if key else None
    if not uid:
        bad_keys.add(ip)
        raise ApiError("Invalid API key")
    per_key.hit(str(uid))
    return uid


async def _services() -> list[dict]:
    async with transaction() as db:
        rows = await _rows(db, None, False)
        cancellable = {r["id"] for r in await db.fetch_all("""
            select s.id from services s join provider_services ps
              on ps.provider_id = s.provider_id and ps.provider_service_id = s.provider_service_id
             where coalesce(ps.cancel, false)""")}
    return [{
        "service": r["id"],
        "name": r["name"],
        "type": "Custom Comments" if r["custom_comments"] else "Default",
        "category": f"{PLATFORMS.get(r['platform'], r['platform'].title())} {r['category'] or 'Other'}",
        "rate": f"{r['price_per_1k_php']:.2f}",
        "min": r["min"],
        "max": r["max"],
        "refill": bool(r["refill_days"]),
        "cancel": r["id"] in cancellable,
    } for r in rows]


async def _status(uid: int, ids: list[int]) -> dict[int, dict]:
    async with transaction() as db:
        rows = await db.fetch_all("""
            select id, price_php, start_count, remains, status from orders
             where user_id = :u and id = ANY(CAST(:ids AS bigint[]))
        """, {"u": uid, "ids": ids})
    return {r["id"]: {"charge": f"{float(r['price_php']):.2f}", "start_count": str(r["start_count"] or 0),
                      "status": STATUS.get(r["status"], r["status"]), "remains": str(r["remains"] or 0),
                      "currency": "PHP"} for r in rows}


async def _run(p: dict, request: Request):
    uid = await _user_for(str(p.get("key") or "").strip(), request)
    action = str(p.get("action") or "").strip().lower()

    if action == "balance":
        async with transaction() as db:
            return {"balance": f"{await balance_of(db, uid):.2f}", "currency": "PHP"}

    if action == "services":
        return await _services()

    if action == "add":
        link = str(p.get("link") or "").strip()
        if not link.startswith(("https://", "http://")) or len(link) > 500:
            raise ApiError("Incorrect link: it must start with https://")
        comments = p.get("comments")
        qty = _int(p.get("quantity"), "quantity") if p.get("quantity") not in (None, "") else 0
        if qty <= 0 and not comments:
            raise ApiError("Incorrect quantity")
        res = await place_order(uid, _int(p.get("service"), "service"), link, max(qty, 1),
                                str(comments).replace("\\n", "\n") if comments else None)
        return {"order": res["id"]}

    if action == "status":
        if p.get("orders"):
            ids = _ids(p["orders"], "order ID")
            found = await _status(uid, ids)
            return {str(i): found.get(i, {"error": "Incorrect order ID"}) for i in ids}
        oid = _int(p.get("order"), "order ID")
        found = await _status(uid, [oid])
        if oid not in found:
            raise ApiError("Incorrect order ID")
        return found[oid]

    if action == "refill":
        async def one(oid):
            try:
                return await refill_order(oid, uid)
            except HTTPException as e:
                return {"error": e.detail}
        if p.get("orders"):
            return [{"order": i, "refill": await one(i)} for i in _ids(p["orders"], "order ID")]
        res = await one(_int(p.get("order"), "order ID"))
        if isinstance(res, dict):
            raise ApiError(res["error"])
        return {"refill": res}

    if action == "refill_status":
        ids = _ids(p.get("refills") or p.get("refill"), "refill ID")
        async with transaction() as db:
            rows = await db.fetch_all("""
                select pr.id, pr.status from provider_refills pr join orders o on o.id = pr.order_id
                 where o.user_id = :u and pr.id = ANY(CAST(:ids AS bigint[]))
            """, {"u": uid, "ids": ids})
        found = {r["id"]: REFILL_STATUS.get(r["status"], r["status"]) for r in rows}
        if p.get("refills"):
            return [{"refill": i, "status": found[i] if i in found else {"error": "Refill not found"}} for i in ids]
        if ids[0] not in found:
            raise ApiError("Refill not found")
        return {"status": found[ids[0]]}

    if action == "cancel":
        out = []
        for i in _ids(p.get("orders") or p.get("order"), "order ID"):
            try:
                await cancel_order(i, uid)
                out.append({"order": i, "cancel": 1})
            except HTTPException as e:
                out.append({"order": i, "cancel": {"error": e.detail}})
        return out

    raise ApiError("Incorrect action")


@router.post("/api/v2")
async def api_v2(request: Request):
    try:
        return await _run(await _params(request), request)
    except ApiError as e:
        return {"error": str(e)}
    except HTTPException as e:
        if e.status_code == 429:
            return JSONResponse({"error": e.detail}, status_code=429)
        return {"error": e.detail}
