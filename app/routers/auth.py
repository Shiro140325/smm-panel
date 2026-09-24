from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError

from app.db import DB, get_db
from app.security import (balance_of, clear_session, current_user, hash_password, issue_session,
                          verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


@router.post("/register")
async def register(body: Credentials, response: Response, db: DB = Depends(get_db)):
    try:
        user = await db.fetch_one(
            "insert into users (email, password_hash) values (:e, :h) returning id, email",
            {"e": body.email.lower(), "h": hash_password(body.password)},
        )
    except IntegrityError:
        raise HTTPException(409, "Email already registered")
    issue_session(response, user["id"])
    return {"id": user["id"], "email": user["email"]}


@router.post("/login")
async def login(body: Credentials, response: Response, db: DB = Depends(get_db)):
    user = await db.fetch_one(
        "select id, email, password_hash from users where lower(email) = lower(:e)", {"e": body.email}
    )
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    issue_session(response, user["id"])
    return {"id": user["id"], "email": user["email"]}


@router.post("/logout")
async def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user), db: DB = Depends(get_db)):
    return {**user, "balance_php": await balance_of(db, user["id"])}
