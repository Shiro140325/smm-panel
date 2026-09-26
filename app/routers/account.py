"""Customer account extras: referral (affiliate) stats and the reseller API key."""
import hashlib
import secrets

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.db import DB, get_db
from app.routers.auth import new_ref_code
from app.security import current_user

router = APIRouter(prefix="/account", tags=["account"])


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


@router.get("/affiliate")
async def affiliate(user: dict = Depends(current_user), db: DB = Depends(get_db)):
    code = await db.fetch_val("select ref_code from users where id = :u", {"u": user["id"]})
    while not code:   # accounts made before the referral program get a code on first visit
        code = await db.fetch_val("""
            update users set ref_code = :c where id = :u and ref_code is null
              and not exists (select 1 from users where ref_code = :c)
            returning ref_code
        """, {"c": new_ref_code(), "u": user["id"]}) or \
            await db.fetch_val("select ref_code from users where id = :u", {"u": user["id"]})
    stats = await db.fetch_one("""
        select (select count(*) from users where referred_by = :u) as referred,
               (select count(distinct t.user_id) from topups t join users r on r.id = t.user_id
                 where r.referred_by = :u and t.status = 'credited') as paying,
               coalesce((select sum(delta) from ledger where user_id = :u and reason = 'referral'), 0) as earned_php
    """, {"u": user["id"]})
    recent = await db.fetch_all("""
        select l.created_at, l.delta as commission_php, t.amount_php as topup_php, r.email
          from ledger l
          join topups t on t.id::text = l.ref
          join users r on r.id = t.user_id
         where l.user_id = :u and l.reason = 'referral'
         order by l.created_at desc limit 20
    """, {"u": user["id"]})
    s = get_settings()
    return {
        "code": code,
        "link": f"{s.frontend_origin}/?ref={code}",
        "pct": s.referral_pct,
        "referred": stats["referred"],
        "paying": stats["paying"],
        "earned_php": float(stats["earned_php"]),
        "recent": [{**dict(r), "email": _mask(r["email"])} for r in recent],
    }


@router.get("/api-key")
async def api_key_status(user: dict = Depends(current_user), db: DB = Depends(get_db)):
    has = await db.fetch_val("select api_key_hash is not null from users where id = :u", {"u": user["id"]})
    return {"has_key": bool(has), "url": get_settings().base_url + "/api/v2"}


@router.post("/api-key")
async def new_api_key(user: dict = Depends(current_user), db: DB = Depends(get_db)):
    """Create (or replace) the API key. It is shown once; only its hash is stored."""
    key = secrets.token_hex(16)
    await db.execute("update users set api_key_hash = :h where id = :u", {"h": hash_api_key(key), "u": user["id"]})
    return {"key": key}


@router.delete("/api-key")
async def revoke_api_key(user: dict = Depends(current_user), db: DB = Depends(get_db)):
    await db.execute("update users set api_key_hash = null where id = :u", {"u": user["id"]})
    return {"ok": True}
