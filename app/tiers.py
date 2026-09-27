"""Customer tiers: Member → Pro → Elite, earned by lifetime spending on website orders and kept forever.

Spending = what was charged for orders placed on the site (not the reseller API), minus refunds.
Because spending after refunds only goes down when an order is refunded, a tier is also
remembered once reached (users.tier_max), so it's never lost.
"""
import math

from app.config import get_settings

# name, spending needed (PHP), discount on website orders (%), referral commission bump (+ points), top-up bonus (%)
TIERS = [
    {"name": "member", "at": 0, "discount_pct": 0, "referral_bump": 0, "topup_bonus_pct": 0},
    {"name": "pro", "at": 10_000, "discount_pct": 3, "referral_bump": 1, "topup_bonus_pct": 0},
    {"name": "elite", "at": 25_000, "discount_pct": 5, "referral_bump": 2, "topup_bonus_pct": 2},
]
BY_NAME = {t["name"]: t for t in TIERS}
RANK = {t["name"]: i for i, t in enumerate(TIERS)}
TOPUP_BONUS_MIN = 1_000   # Elite bonus applies to top-ups of at least this much


def tier_for_spent(spent: float) -> dict:
    tier = TIERS[0]
    for t in TIERS:
        if spent >= t["at"]:
            tier = t
    return tier


def higher(a: str | None, b: str | None) -> str:
    return max((a or "member", b or "member"), key=lambda n: RANK.get(n, 0))


async def spent_php(db, user_id: int) -> float:
    """Lifetime website-order spending after refunds."""
    return float(await db.fetch_val("""
        select coalesce(sum(o.price_php - coalesce(r.refunded, 0)), 0)
          from orders o
          left join (select ref, sum(delta) as refunded from ledger
                      where user_id = :u and reason = 'refund' group by ref) r on r.ref = o.id::text
         where o.user_id = :u and o.source = 'web'
    """, {"u": user_id}))


async def current_tier(db, user_id: int) -> dict:
    """The customer's tier (never lower than one already reached), with spending and the next step."""
    spent = await spent_php(db, user_id)
    row = await db.fetch_one("select tier_max, tier_seen from users where id = :u", {"u": user_id})
    earned = tier_for_spent(spent)["name"]
    name = higher(earned, row["tier_max"] if row else None)
    if row and name != (row["tier_max"] or "member"):
        await db.execute("update users set tier_max = :t where id = :u", {"t": name, "u": user_id})
    t = BY_NAME[name]
    nxt = TIERS[RANK[name] + 1] if RANK[name] + 1 < len(TIERS) else None
    return {
        "name": name,
        "discount_pct": t["discount_pct"],
        "referral_pct": get_settings().referral_pct + t["referral_bump"],
        "topup_bonus_pct": t["topup_bonus_pct"],
        "spent_php": round(spent, 2),
        "next": {"name": nxt["name"], "at_php": nxt["at"]} if nxt else None,
        "seen": (row["tier_seen"] if row else None),
    }


def discounted_per_1k(per_1k: float, discount_pct: float) -> float:
    """Price per 1,000 after a tier discount, rounded up to the centavo (same rule as dashboard.js)."""
    if not discount_pct:
        return per_1k
    return math.ceil(round(per_1k * (100 - discount_pct), 6)) / 100
