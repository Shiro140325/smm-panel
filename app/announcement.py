"""The announcement bar: one short line at the top of the dashboard (logged-in customers only), edited from /admin."""
import time

from fastapi import APIRouter, Depends

from app.db import DB, get_db
from app.security import current_user

MAX_CHARS = 140   # including spaces: one line on a computer, two or three on a phone
KEY = "announcement"
router = APIRouter(tags=["announcement"])
_cache: tuple[float, dict] | None = None


async def read(db: DB) -> dict:
    row = await db.fetch_one("select value, updated_at from site_settings where key = :k", {"k": KEY})
    return {"text": row["value"], "updated_at": row["updated_at"]} if row else {"text": "", "updated_at": None}


async def save(db: DB, text: str) -> dict:
    global _cache
    text = " ".join((text or "").split())   # one line: newlines and repeated spaces collapse
    if text:
        await db.execute("""
            insert into site_settings (key, value) values (:k, :v)
            on conflict (key) do update set value = excluded.value, updated_at = now()
        """, {"k": KEY, "v": text[:MAX_CHARS]})
    else:
        await db.execute("delete from site_settings where key = :k", {"k": KEY})
    _cache = None
    return await read(db)


@router.get("/announcement")
async def get_announcement(user: dict = Depends(current_user), db: DB = Depends(get_db)):
    """Logged-in customers only. Every dashboard load asks for this, so it's cached for 30 seconds (cleared on save)."""
    global _cache
    if _cache is None or time.monotonic() - _cache[0] >= 30:
        _cache = (time.monotonic(), await read(db))
    return _cache[1]
