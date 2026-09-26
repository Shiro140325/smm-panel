import logging
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError

from app.db import DB, get_db
from app.ratelimit import Limiter, client_ip
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
    return {"id": user["id"], "email": user["email"]}


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
    issue_session(response, user["id"])
    return {"id": user["id"], "email": user["email"]}


@router.post("/logout")
async def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user), db: DB = Depends(get_db)):
    return {**user, "balance_php": await balance_of(db, user["id"])}
