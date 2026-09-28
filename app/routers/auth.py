import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError

from app import verify
from app.config import get_settings
from app.db import DB, get_db, transaction
from app.ratelimit import Limiter, client_ip
from app.tiers import current_tier
from app.trial import device_of, trial_for
from app.security import (balance_of, clear_session, current_user, hash_password, issue_session,
                          verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("auth")

# Wrong passwords: 5 per account and 20 per IP per 15 minutes (an IP can be shared, e.g. mobile data).
# Sign-ups: 5 per IP per hour. Env overrides exist for tests.
_WINDOW = 15 * 60
login_fails_email = Limiter(int(os.environ.get("LOGIN_MAX_FAILS_EMAIL", "5")), _WINDOW,
                            "Too many wrong passwords for this account. Try again in 15 minutes.")
login_fails_ip = Limiter(int(os.environ.get("LOGIN_MAX_FAILS_IP", "20")), _WINDOW,
                         "Too many wrong passwords from your network. Try again in 15 minutes.")
signups_ip = Limiter(int(os.environ.get("SIGNUPS_PER_IP_HOUR", "5")), 3600,
                     "Too many new accounts from your network. Try again later.")


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class CodeIn(BaseModel):
    code: str = Field(max_length=20)


class EmailIn(BaseModel):
    email: EmailStr


class RegisterIn(Credentials):
    ref: str | None = Field(default=None, max_length=32)   # referral code from the signup link


REF_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alikes (0/o, 1/l/i)


def new_ref_code() -> str:
    return "".join(secrets.choice(REF_ALPHABET) for _ in range(8))


@router.post("/register")
async def register(body: RegisterIn, request: Request, response: Response, db: DB = Depends(get_db)):
    signups_ip.hit(client_ip(request))
    referrer = None
    if body.ref:
        referrer = await db.fetch_val("select id from users where ref_code = :c", {"c": body.ref.strip().lower()})
    try:
        user = await db.fetch_one(
            "insert into users (email, password_hash, ref_code, referred_by) values (:e, :h, :c, :r) returning id, email",
            {"e": body.email.lower(), "h": hash_password(body.password), "c": new_ref_code(), "r": referrer},
        )
    except IntegrityError:
        raise HTTPException(409, "Email already registered")
    issue_session(response, user["id"])
    return {"id": user["id"], "email": user["email"], "email_verified": False, "verify_required": verify.required(),
            "trial": await trial_for(db, user["id"], device_of(request), client_ip(request))}


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response, db: DB = Depends(get_db)):
    ip, email = client_ip(request), "e:" + body.email.lower()
    login_fails_ip.check(ip)
    login_fails_email.check(email)
    user = await db.fetch_one(
        "select id, email, password_hash from users where lower(email) = lower(:e)", {"e": body.email}
    )
    if not user or not verify_password(body.password, user["password_hash"]):
        login_fails_ip.add(ip)
        login_fails_email.add(email)
        log.warning("login failed for %s from %s", body.email.lower(), ip)
        raise HTTPException(401, "Wrong email or password")
    login_fails_email.reset(email)
    if not verify.required():
        issue_session(response, user["id"])
        return {"id": user["id"], "email": user["email"], "code_required": False}
    # second step: a code emailed to the account; the session starts only after it
    resend_in = int(verify.RESEND_GAP.total_seconds())
    try:
        await verify.issue_code(db, user["id"], "login", user["email"])
    except HTTPException as e:   # a code went out under a minute ago: that one still works
        wait = re.match(r"Wait (\d+) seconds", str(e.detail))
        if not wait:
            raise
        resend_in = int(wait.group(1))
    _set_pending(response, user["id"])
    return {"code_required": True, "sent_to": _mask_to(user["email"]), "resend_in": resend_in}


PENDING = "login_pending"
PENDING_MINUTES = 15


def _mask_to(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'•' * max(1, len(name) - 2)}@{domain}"


def _set_pending(response: Response, user_id: int) -> None:
    s = get_settings()
    token = jwt.encode({"sub": str(user_id), "pl": 1, "exp": datetime.now(timezone.utc) + timedelta(minutes=PENDING_MINUTES)},
                       s.jwt_secret, algorithm="HS256")
    response.set_cookie(PENDING, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                        max_age=PENDING_MINUTES * 60, path="/auth")


def _pending_user(request: Request) -> int:
    try:
        payload = jwt.decode(request.cookies.get(PENDING) or "", get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Your login timed out. Enter your password again.")
    if payload.get("pl") != 1:
        raise HTTPException(401, "Your login timed out. Enter your password again.")
    return int(payload["sub"])


@router.post("/login/resend")
async def login_resend(request: Request, db: DB = Depends(get_db)):
    uid = _pending_user(request)
    email = await db.fetch_val("select email from users where id = :u", {"u": uid})
    await verify.issue_code(db, uid, "login", email)
    return {"sent_to": _mask_to(email), "resend_in": int(verify.RESEND_GAP.total_seconds())}


@router.post("/login/verify")
async def login_verify(body: CodeIn, request: Request, response: Response):
    """The emailed login code → the session. It also proves the email, so an unverified account is verified."""
    uid = _pending_user(request)

    async def apply(db, rows):
        await db.execute("update users set email_verified_at = coalesce(email_verified_at, now()) where id = :u", {"u": uid})

    await verify.use_codes(uid, {"login": body.code}, apply)
    response.delete_cookie(PENDING, path="/auth")
    issue_session(response, uid)
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/me")
async def me(request: Request, user: dict = Depends(current_user), db: DB = Depends(get_db)):
    verified = await db.fetch_val("select email_verified_at is not null from users where id = :u", {"u": user["id"]})
    return {**user, "balance_php": await balance_of(db, user["id"]), "tier": await current_tier(db, user["id"]),
            "email_verified": bool(verified), "verify_required": verify.required(),
            "trial": await trial_for(db, user["id"], device_of(request), client_ip(request))}


@router.post("/verify/send")
async def verify_send(user: dict = Depends(current_user)):
    """Email a 6-digit code (the dashboard asks for one right after sign-up)."""
    if not verify.required():
        raise HTTPException(400, "Email verification isn't turned on")
    return await verify.send_code(user["id"])


@router.post("/verify/email")
async def verify_change_email(body: EmailIn, user: dict = Depends(current_user)):
    """Mistyped email at sign-up: change it (only while unverified) and send a code there."""
    if not verify.required():
        raise HTTPException(400, "Email verification isn't turned on")
    return {**await verify.change_email(user["id"], body.email), "email": body.email.lower()}


@router.post("/verify")
async def verify_check(body: CodeIn, request: Request, user: dict = Depends(current_user)):
    await verify.check_code(user["id"], body.code)
    async with transaction() as db:
        return {"email_verified": True, "trial": await trial_for(db, user["id"], device_of(request), client_ip(request))}
