"""Thin async DB layer over SQLAlchemy Core + asyncpg. Raw SQL with :named params."""
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import get_settings


def _asyncpg_url(url: str) -> tuple[str, dict]:
    """Neon gives postgresql://...?sslmode=require&channel_binding=require.
    asyncpg doesn't accept those query params, so strip them and pass ssl via connect_args."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    scheme = "postgresql+asyncpg"
    connect_args: dict = {"statement_cache_size": 0}   # safe with Neon's pooled (pgbouncer) endpoint
    if sslmode in ("require", "verify-ca", "verify-full"):
        connect_args["ssl"] = "require"
    query["prepared_statement_cache_size"] = "0"
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), "")), connect_args


_url, _connect_args = _asyncpg_url(get_settings().database_url)
engine = create_async_engine(_url, connect_args=_connect_args, pool_size=5, max_overflow=5, pool_pre_ping=True)


class DB:
    """Wraps one connection; use inside `transaction()` or `connection()`."""

    def __init__(self, conn: AsyncConnection):
        self.conn = conn

    async def execute(self, sql: str, params: dict | None = None):
        return await self.conn.execute(text(sql), params or {})

    async def fetch_all(self, sql: str, params: dict | None = None) -> list[dict]:
        res = await self.conn.execute(text(sql), params or {})
        return [dict(r._mapping) for r in res]

    async def fetch_one(self, sql: str, params: dict | None = None) -> dict | None:
        res = await self.conn.execute(text(sql), params or {})
        row = res.first()
        return dict(row._mapping) if row else None

    async def fetch_val(self, sql: str, params: dict | None = None):
        res = await self.conn.execute(text(sql), params or {})
        return res.scalar()


@asynccontextmanager
async def transaction():
    async with engine.begin() as conn:
        yield DB(conn)


async def get_db():
    """FastAPI dependency: one transaction per request, committed on success."""
    async with engine.begin() as conn:
        yield DB(conn)
