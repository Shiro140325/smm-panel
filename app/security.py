from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request, Response

from app.config import get_settings
from app.db import DB, get_db

COOKIE = "session"
_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerificationError:
        return False


def issue_session(response: Response, user_id: int) -> None:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=s.jwt_ttl_hours)
    token = jwt.encode({"sub": str(user_id), "exp": exp}, s.jwt_secret, algorithm="HS256")
    response.set_cookie(
        COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax",
        max_age=s.jwt_ttl_hours * 3600, path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


async def current_user(request: Request, db: DB = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not logged in")
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired")
    user = await db.fetch_one(
        "select id, email, is_admin from users where id = :id", {"id": int(payload["sub"])}
    )
    if not user:
        raise HTTPException(401, "Not logged in")
    return user


async def balance_of(db: DB, user_id: int) -> float:
    return float(await db.fetch_val(
        "select coalesce(sum(delta), 0) from ledger where user_id = :u", {"u": user_id}
    ))
