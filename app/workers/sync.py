"""Background sync: order statuses, refill statuses, provider catalogs.

Runs in-process (see app.main lifespan) every SYNC_INTERVAL_SECONDS.
Every step is idempotent, so overlapping or repeated runs are safe.
"""
import json
import logging
import time

from app import fx
from app.config import get_settings
from app.db import transaction
from app.payments import reconcile_pending_topups
from app.providers.smm_client import SMMClient

log = logging.getLogger("sync")

ORDER_STATUS = {
    "pending": "pending", "in progress": "in_progress", "processing": "in_progress",
    "completed": "completed", "partial": "partial", "canceled": "canceled", "cancelled": "canceled",
}
REFILL_STATUS = {"completed": "completed", "rejected": "rejected"}   # anything else stays pending

CATALOG_EVERY_SECONDS = 6 * 3600
_last_catalog_sync: dict[int, float] = {}


async def sync_orders(client: SMMClient, provider_id: int, user_id: int | None = None) -> int:
    async with transaction() as db:
        rows = await db.fetch_all("""
            select id, provider_order_id from orders
             where provider_id = :p and status in ('pending', 'in_progress')
               and provider_order_id is not null
               and (CAST(:u AS bigint) is null or user_id = CAST(:u AS bigint))
        """, {"p": provider_id, "u": user_id})
    if not rows:
        return 0
    by_pid = {str(r["provider_order_id"]): r["id"] for r in rows}
    res = await client.statuses(by_pid.keys())

    changed = 0
    for pid, s in res.items():
        if pid not in by_pid or not isinstance(s, dict) or "error" in s:
            continue
        status = ORDER_STATUS.get(str(s.get("status", "")).strip().lower(), "in_progress")
        remains = max(int(float(s.get("remains") or 0)), 0)
        start_count = s.get("start_count")
        async with transaction() as db:
            upd = await db.fetch_one("""
                update orders
                   set status = :st, remains = :rem,
                       start_count = coalesce(CAST(:sc AS integer), start_count),
                       cost = coalesce(CAST(:cost AS numeric), cost),
                       completed_at = case when :st in ('completed', 'partial')
                                           then coalesce(completed_at, now()) else completed_at end,
                       updated_at = now()
                 where id = :id and status in ('pending', 'in_progress')
                returning id, user_id, price_php, quantity, status
            """, {"st": status, "rem": remains,
                  "sc": int(float(start_count)) if start_count not in (None, "") else None,
                  "cost": str(s["charge"]) if s.get("charge") not in (None, "") else None,
                  "id": by_pid[pid]})
            if not upd:
                continue
            changed += 1
            # partial/canceled → refund the unfilled portion, once (unique ledger index guards it)
            if status in ("partial", "canceled"):
                share = 1.0 if status == "canceled" else min(remains / upd["quantity"], 1.0)
                refund = round(float(upd["price_php"]) * share, 2)
                if refund > 0:
                    await db.execute("""
                        insert into ledger (user_id, delta, reason, ref)
                        values (:u, :d, 'refund', :r) on conflict do nothing
                    """, {"u": upd["user_id"], "d": refund, "r": str(upd["id"])})
    return changed


async def sync_refills(client: SMMClient, provider_id: int) -> int:
    ignore_days = get_settings().refill_ignore_after_days
    async with transaction() as db:
        rows = await db.fetch_all("""
            select id, provider_refill_id from provider_refills
             where provider_id = :p and status = 'pending'
        """, {"p": provider_id})
    changed = 0
    if rows:
        by_rid = {str(r["provider_refill_id"]): r["id"] for r in rows}
        for item in await client.refill_statuses(by_rid.keys()):
            st = item.get("status")
            rid = str(item.get("refill"))
            if isinstance(st, dict) or rid not in by_rid:
                continue
            mapped = REFILL_STATUS.get(str(st).strip().lower())
            if mapped:
                async with transaction() as db:
                    await db.execute("""
                        update provider_refills set status = :s, resolved_at = now()
                         where id = :id and status = 'pending'
                    """, {"s": mapped, "id": by_rid[rid]})
                changed += 1

    # refills the provider never resolved → 'ignored' (feeds the provider scorecard)
    async with transaction() as db:
        await db.execute("""
            update provider_refills set status = 'ignored', resolved_at = now()
             where provider_id = :p and status = 'pending'
               and requested_at < now() - make_interval(days => :d)
        """, {"p": provider_id, "d": ignore_days})
    return changed


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


async def sync_catalog(client: SMMClient, provider_id: int) -> int:
    services = await client.services()
    by_sid: dict[int, dict] = {}   # dedupe: ON CONFLICT can't touch the same row twice
    for s in services:
        try:
            by_sid[int(s["service"])] = ({
                "sid": int(s["service"]), "name": str(s.get("name") or ""),
                "cat": s.get("category"), "type": s.get("type"),
                "rate": str(float(s.get("rate") or 0)),
                "min": int(float(s.get("min") or 0)), "max": int(float(s.get("max") or 0)),
                "refill": _truthy(s.get("refill")), "cancel": _truthy(s.get("cancel")),
                "raw": s,
            })
        except (KeyError, TypeError, ValueError):
            log.warning("skipping malformed service: %r", s)
    rows = list(by_sid.values())
    if not rows:
        return 0
    # one round trip for the whole catalog (thousands of rows)
    async with transaction() as db:
        await db.execute("""
            insert into provider_services
              (provider_id, provider_service_id, name, category, type, rate,
               min_qty, max_qty, refill, cancel, raw, updated_at)
            select :p, x.sid, x.name, x.cat, x.type, CAST(x.rate AS numeric),
                   x.min, x.max, x.refill, x.cancel, x.raw, now()
              from jsonb_to_recordset(CAST(:rows AS jsonb)) as x(
                     sid bigint, name text, cat text, type text, rate text,
                     min integer, max integer, refill boolean, cancel boolean, raw jsonb)
            on conflict (provider_id, provider_service_id) do update set
              name = excluded.name, category = excluded.category, type = excluded.type,
              rate = excluded.rate, min_qty = excluded.min_qty, max_qty = excluded.max_qty,
              refill = excluded.refill, cancel = excluded.cancel, raw = excluded.raw,
              updated_at = now()
        """, {"p": provider_id, "rows": json.dumps(rows)})
    return len(rows)


async def flag_stuck_orders() -> None:
    """Orders debited but never confirmed upstream (crash mid-request) → manual review, not auto-refund."""
    async with transaction() as db:
        await db.execute("""
            update orders set status = 'needs_review', updated_at = now()
             where status = 'creating' and created_at < now() - interval '10 minutes'
        """)


async def sync_user_orders(user_id: int) -> int:
    """On-demand refresh of one customer's open orders (called when they view Orders)."""
    async with transaction() as db:
        providers = await db.fetch_all("""
            select distinct p.* from providers p join orders o on o.provider_id = p.id
             where p.active and o.user_id = :u and o.status in ('pending', 'in_progress')
        """, {"u": user_id})
    n = 0
    for p in providers:
        try:
            n += await sync_orders(SMMClient.for_provider(p), p["id"], user_id=user_id)
        except Exception:
            log.exception("on-demand sync for user %s / %s failed", user_id, p["name"])
    return n


async def run_sync_once() -> None:
    try:
        await fx.refresh()          # no-op unless the cached rate is >6h old
    except Exception:
        log.exception("fx refresh failed")
    async with transaction() as db:
        providers = await db.fetch_all("select * from providers where active")
    for p in providers:
        try:
            client = SMMClient.for_provider(p)
        except Exception as e:
            log.warning("provider %s skipped: %s", p["name"], e)
            continue
        for step in (sync_orders, sync_refills):
            try:
                n = await step(client, p["id"])
                if n:
                    log.info("%s %s: %d updated", p["name"], step.__name__, n)
            except Exception:
                log.exception("%s %s failed", p["name"], step.__name__)
        if time.monotonic() - _last_catalog_sync.get(p["id"], -1e9) > CATALOG_EVERY_SECONDS:
            try:
                n = await sync_catalog(client, p["id"])
                _last_catalog_sync[p["id"]] = time.monotonic()
                log.info("%s catalog: %d services", p["name"], n)
            except Exception:
                log.exception("%s catalog sync failed", p["name"])
    try:
        await flag_stuck_orders()
    except Exception:
        log.exception("flag_stuck_orders failed")
    try:
        n = await reconcile_pending_topups()   # safety net for missed PayMongo webhooks
        if n:
            log.info("credited %d top-up(s) via checkout lookup", n)
    except Exception:
        log.exception("reconcile_pending_topups failed")
