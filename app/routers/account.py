"""Customer account extras: referral (affiliate) stats, the reseller API key, and account settings
(email and password changes, each confirmed with emailed codes)."""
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app import verify
from app.config import get_settings
from app.db import DB, get_db, transaction
from app.ratelimit import client_ip
from app.routers.auth import new_ref_code
from app.security import current_user, hash_password, issue_session, verify_password
from app.tiers import current_tier

router = APIRouter(prefix="/account", tags=["account"])


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


@router.get("/affiliate")
async def affiliate(user: dict = Depends(current_user), db: DB = Depends(get_db, scope="function")):
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
        "pct": (await current_tier(db, user["id"]))["referral_pct"],   # 5% Member, 6% Pro, 7% Elite
        "referred": stats["referred"],
        "paying": stats["paying"],
        "earned_php": float(stats["earned_php"]),
        "recent": [{**dict(r), "email": _mask(r["email"])} for r in recent],
    }


@router.get("/api-key")
async def api_key_status(user: dict = Depends(current_user), db: DB = Depends(get_db, scope="function")):
    has = await db.fetch_val("select api_key_hash is not null from users where id = :u", {"u": user["id"]})
    return {"has_key": bool(has), "url": get_settings().base_url + "/api/v2"}


@router.post("/api-key")
async def new_api_key(user: dict = Depends(current_user), db: DB = Depends(get_db, scope="function")):
    """Create (or replace) the API key. It is shown once; only its hash is stored."""
    key = secrets.token_hex(16)
    await db.execute("update users set api_key_hash = :h where id = :u", {"h": hash_api_key(key), "u": user["id"]})
    return {"key": key}


@router.delete("/api-key")
async def revoke_api_key(user: dict = Depends(current_user), db: DB = Depends(get_db, scope="function")):
    await db.execute("update users set api_key_hash = null where id = :u", {"u": user["id"]})
    return {"ok": True}


@router.post("/tier-seen")
async def tier_seen(user: dict = Depends(current_user), db: DB = Depends(get_db, scope="function")):
    """The customer closed their tier's introduction card: don't show it again (on any device)."""
    tier = await current_tier(db, user["id"])
    await db.execute("update users set tier_seen = :t where id = :u", {"t": tier["name"], "u": user["id"]})
    return {"seen": tier["name"]}


# ---------------------------------------------------------------- settings

def _mask_to(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'•' * max(1, len(name) - 2)}@{domain}"


class PasswordCodeIn(BaseModel):
    current_password: str = Field(max_length=128)


class PasswordIn(PasswordCodeIn):
    new_password: str = Field(min_length=8, max_length=128)
    code: str = Field(max_length=20)


class EmailStartIn(BaseModel):
    new_email: EmailStr


class EmailConfirmIn(BaseModel):
    old_code: str = Field(max_length=20)
    new_code: str = Field(max_length=20)


async def _check_password(db, user_id: int, password: str, request: Request) -> None:
    from app.routers.auth import login_fails_email, login_fails_ip   # same lockout as logging in
    ip, key = client_ip(request), f"u:{user_id}"
    login_fails_ip.check(ip)
    login_fails_email.check(key)
    h = await db.fetch_val("select password_hash from users where id = :u", {"u": user_id})
    if not verify_password(password, h):
        login_fails_ip.add(ip)
        login_fails_email.add(key)
        raise HTTPException(400, "Your current password is wrong")


@router.get("/settings")
async def settings(user: dict = Depends(current_user), db: DB = Depends(get_db, scope="function")):
    row = await db.fetch_one("select email, created_at, email_verified_at, password_changed_at from users where id = :u",
                             {"u": user["id"]})
    pending = await db.fetch_val("select new_email from account_codes where user_id = :u and purpose = 'email_new' "
                                 "and expires_at > now()", {"u": user["id"]})
    return {**dict(row), "pending_email": pending}


@router.post("/password/code")
async def password_code(body: PasswordCodeIn, request: Request, user: dict = Depends(current_user)):
    """Step 1: check the current password, then email a code."""
    async with transaction() as db:
        await _check_password(db, user["id"], body.current_password, request)
    async with transaction() as db:
        await verify.issue_code(db, user["id"], "password", user["email"])
    return {"sent_to": _mask_to(user["email"]), "resend_in": int(verify.RESEND_GAP.total_seconds())}


@router.post("/password")
async def change_password(body: PasswordIn, request: Request, response: Response, user: dict = Depends(current_user)):
    """Step 2: current password + the emailed code → new password. Other devices are logged out."""
    async with transaction() as db:
        await _check_password(db, user["id"], body.current_password, request)
    if body.new_password == body.current_password:
        raise HTTPException(400, "The new password is the same as the current one")

    async def apply(db, rows):
        await db.execute("update users set password_hash = :h, password_changed_at = now() where id = :u",
                         {"h": hash_password(body.new_password), "u": user["id"]})

    await verify.use_codes(user["id"], {"password": body.code}, apply)
    issue_session(response, user["id"])   # this device stays logged in
    return {"ok": True}


@router.post("/email/start")
async def email_start(body: EmailStartIn, user: dict = Depends(current_user)):
    """Step 1: a code to the current email and a code to the new one."""
    new = body.new_email.lower()
    if new == user["email"].lower():
        raise HTTPException(400, "That's already your email")
    async with transaction() as db:
        if await db.fetch_val("select 1 from users where lower(email) = :e", {"e": new}):
            raise HTTPException(409, "That email already has an account")
        await verify.issue_code(db, user["id"], "email_old", user["email"], new_email=new)
        await verify.issue_code(db, user["id"], "email_new", new, new_email=new)
    return {"old_to": _mask_to(user["email"]), "new_to": new, "resend_in": int(verify.RESEND_GAP.total_seconds())}


@router.post("/email")
async def email_confirm(body: EmailConfirmIn, user: dict = Depends(current_user)):
    """Step 2: both codes → the new email becomes the account's (already verified)."""
    changed = {}

    async def apply(db, rows):
        new = rows["email_new"]["new_email"]
        if not new or rows["email_old"]["new_email"] != new:
            raise HTTPException(400, "Start the email change again")
        if await db.fetch_val("select 1 from users where lower(email) = :e and id <> :u", {"e": new, "u": user["id"]}):
            raise HTTPException(409, "That email already has an account")
        await db.execute("update users set email = :e, email_verified_at = now() where id = :u", {"e": new, "u": user["id"]})
        # a login code sent to the old address is useless now: the next login sends one to the new address
        await db.execute("delete from account_codes where user_id = :u and purpose = 'login'", {"u": user["id"]})
        changed["email"] = new

    await verify.use_codes(user["id"], {"email_old": body.old_code, "email_new": body.new_code}, apply)
    return {"ok": True, "email": changed["email"]}
