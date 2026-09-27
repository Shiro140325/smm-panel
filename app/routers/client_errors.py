"""Browser errors reported by the dashboard, so problems on devices we can't test on can be diagnosed."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.db import DB, get_db
from app.ratelimit import Limiter, client_ip

router = APIRouter(tags=["client errors"], include_in_schema=False)
_limit = Limiter(20, 3600, "Too many reports")


class ErrorIn(BaseModel):
    page: str = Field(default="", max_length=300)
    message: str = Field(default="", max_length=2000)


@router.post("/client-error")
async def report(body: ErrorIn, request: Request, db: DB = Depends(get_db)):
    _limit.hit(client_ip(request))
    await db.execute("insert into client_errors (page, message, user_agent) values (:p, :m, :ua)",
                     {"p": body.page, "m": body.message, "ua": request.headers.get("user-agent", "")[:400]})
    return {"ok": True}
