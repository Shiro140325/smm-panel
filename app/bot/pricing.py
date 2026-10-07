"""Bot menu prices: each item has the owner's price list ([amount, price] pairs, e.g. 100 → ₱20, 1K → ₱125).

A listed amount costs exactly its listed price. Any other amount is priced at the per-unit rate of the
nearest listed amount (a tie goes to the bigger one), and never costs more than a bigger amount would:
if 3,000 comes to ₱90, then 2,500 is ₱90 at most too. Totals are rounded up to the whole peso.
"""
import math


def clean(prices) -> list[tuple[int, float]]:
    """[[qty, price], ...] or [{"qty", "price"}, ...] → sorted [(qty, price)] with one price per amount."""
    out: dict[int, float] = {}
    for p in prices or []:
        q, v = (p.get("qty"), p.get("price")) if isinstance(p, dict) else (p[0], p[1])
        if int(q) > 0 and float(v) > 0:
            out[int(q)] = float(v)
    return sorted(out.items())


def _nearest_rate(tiers: list[tuple[int, float]], qty: int) -> float:
    q, p = min(tiers, key=lambda t: (abs(t[0] - qty), -t[0]))
    return p / q


def price_for(prices, qty: int) -> float:
    tiers = clean(prices)
    if not tiers or qty <= 0:
        raise ValueError("no price list")
    for q, p in tiers:
        if q == qty:
            return float(math.ceil(round(p, 6)))
    best = _nearest_rate(tiers, qty) * qty
    # where the nearest listed amount switches to a bigger one, the price can drop: cap at those points
    for (a, _), (b, _) in zip(tiers, tiers[1:]):
        start = math.ceil((a + b) / 2)
        if start > qty:
            best = min(best, _nearest_rate(tiers, start) * start)
    return float(max(1, math.ceil(round(best, 6))))


def short_qty(q: int) -> str:
    if q >= 1000 and q % 100 == 0:
        k = q / 1000
        return f"{k:g}K"
    return f"{q:,}"


def price_list_text(prices, sep: str = " · ") -> str:
    """100 = ₱20 · 500 = ₱65 · 1K = ₱125   (a single 1-unit price reads "₱2 each")."""
    tiers = clean(prices)
    if len(tiers) == 1 and tiers[0][0] == 1:
        return f"₱{tiers[0][1]:g} each"
    return sep.join(f"{short_qty(q)} = ₱{p:,.2f}".replace(".00", "") for q, p in tiers)
