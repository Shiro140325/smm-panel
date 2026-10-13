"""Pause switch for sending orders to the provider (edited from /admin).

While paused, customers can still order: the order is charged and saved as 'queued' (shown to them
as Pending) instead of being sent. Switching back on sends the backlog oldest first, one per second
(app.routers.orders.drain_queue). New orders that arrive while a backlog is still being sent join the
end of the queue, so nobody jumps the line.

Low funds hold: when the provider refuses an order because our balance there is too low, the order goes
back in the queue (still Pending for the customer) and sending holds by itself until the balance covers
it again (app.routers.orders.check_low_funds), separately from the owner's own pause switch.
"""
import re

KEY = "orders_paused"
FUNDS_KEY = "orders_low_funds"   # value: the provider's message
_FUNDS_RE = re.compile(r"(not enough|insufficient|low)\b.{0,20}\b(funds?|balance)|balance.{0,20}\b(insufficient|not enough|too low)", re.I)


def is_funds_error(message: str) -> bool:
    return bool(_FUNDS_RE.search(message or ""))


async def is_paused(db) -> bool:
    return await db.fetch_val("select value from site_settings where key = :k", {"k": KEY}) == "1"


async def set_paused(db, paused: bool) -> None:
    if paused:
        await db.execute("""
            insert into site_settings (key, value) values (:k, '1')
            on conflict (key) do update set value = '1', updated_at = now()
        """, {"k": KEY})
    else:
        await db.execute("delete from site_settings where key = :k", {"k": KEY})


async def low_funds(db) -> bool:
    return bool(await db.fetch_val("select 1 from site_settings where key = :k", {"k": FUNDS_KEY}))


async def set_low_funds(db, on: bool, reason: str = "") -> bool:
    """Start or end the low funds hold. Returns True when this call started it."""
    if not on:
        await db.execute("delete from site_settings where key = :k", {"k": FUNDS_KEY})
        return False
    return bool(await db.fetch_val("""
        insert into site_settings (key, value) values (:k, :v) on conflict (key) do nothing returning key
    """, {"k": FUNDS_KEY, "v": (reason or "Not enough funds")[:300]}))


async def is_held(db) -> bool:
    """Nothing goes to the provider: the owner paused it, or the balance there is too low."""
    return await is_paused(db) or await low_funds(db)


async def should_queue(db) -> bool:
    """Held, or a backlog is still being sent."""
    return await is_held(db) or bool(
        await db.fetch_val("select 1 from orders where status = 'queued' limit 1"))


async def state(db) -> dict:
    row = await db.fetch_one("select updated_at from site_settings where key = :k", {"k": KEY})
    funds = await db.fetch_one("select value, updated_at from site_settings where key = :k", {"k": FUNDS_KEY})
    q = await db.fetch_one("""
        select count(*) as n, coalesce(sum(price_php), 0) as php, min(created_at) as oldest
          from orders where status = 'queued'
    """)
    return {"paused": row is not None, "paused_since": row["updated_at"] if row else None,
            "low_funds": funds is not None, "low_funds_since": funds["updated_at"] if funds else None,
            "low_funds_reason": funds["value"] if funds else None,
            "queued": q["n"], "queued_php": float(q["php"]), "oldest_queued_at": q["oldest"]}
