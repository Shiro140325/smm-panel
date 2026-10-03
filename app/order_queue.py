"""Pause switch for sending orders to the provider (edited from /admin).

While paused, customers can still order: the order is charged and saved as 'queued' (shown to them
as Pending) instead of being sent. Switching back on sends the backlog oldest first, one per second
(app.routers.orders.drain_queue). New orders that arrive while a backlog is still being sent join the
end of the queue, so nobody jumps the line.
"""
KEY = "orders_paused"


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


async def should_queue(db) -> bool:
    """Paused, or a backlog is still being sent."""
    return await is_paused(db) or bool(
        await db.fetch_val("select 1 from orders where status = 'queued' limit 1"))


async def state(db) -> dict:
    row = await db.fetch_one("select updated_at from site_settings where key = :k", {"k": KEY})
    q = await db.fetch_one("""
        select count(*) as n, coalesce(sum(price_php), 0) as php, min(created_at) as oldest
          from orders where status = 'queued'
    """)
    return {"paused": row is not None, "paused_since": row["updated_at"] if row else None,
            "queued": q["n"], "queued_php": float(q["php"]), "oldest_queued_at": q["oldest"]}
