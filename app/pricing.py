import math

from app import fx


def fx_to_php(currency: str) -> float:
    c = (currency or "USD").upper()
    if c == "PHP":
        return 1.0
    if c == "USD":
        return fx.usd_to_php()
    raise ValueError(f"Unsupported provider currency: {currency}")


# Markup in pesos, charged like tax brackets on the cost per 1,000: each slice of the cost gets its own
# rate, so cheap services add a few pesos and expensive ones a small percentage, and the price never
# jumps at a bracket edge. (cost in PHP per 1K up to, markup % on that slice)
MARKUP_BRACKETS = [(1, 100.0), (10, 38.0), (30, 24.0), (100, 18.0), (300, 14.0), (1000, 12.0)]
MARKUP_TOP = 10.0            # on the part of the cost above the last bracket
FIXED_MIN_OVER_COST = 1.05   # a fixed price must stay at least 5% above cost to apply


def markup_php(cost_php: float) -> float:
    """Pesos added to a cost of cost_php per 1,000."""
    added, lower = 0.0, 0.0
    for upper, pct in MARKUP_BRACKETS:
        if cost_php <= lower:
            return added
        added += (min(cost_php, upper) - lower) * pct / 100
        lower = upper
    return added + max(cost_php - lower, 0) * MARKUP_TOP / 100


def price_per_1k_php(rate: float, currency: str, markup_pct: float | None, fixed_php: float | None = None) -> float:
    """Customer price per 1,000 in PHP, rounded up to the centavo. A fixed peso price (services.price_php)
    wins over the markup, so it doesn't move with the exchange rate. A service's own markup_pct wins over
    the peso brackets."""
    cost = float(rate) * fx_to_php(currency)
    if fixed_php is not None and float(fixed_php) >= cost * FIXED_MIN_OVER_COST:
        return round(float(fixed_php), 2)
    # (a fixed price that no longer covers the provider's cost is ignored: the markup applies instead)
    raw = cost * (1 + float(markup_pct) / 100) if markup_pct is not None else cost + markup_php(cost)
    return math.ceil(round(raw * 100, 6)) / 100   # round first: 46.400000000000006 must not become 46.41


def order_price_php(per_1k: float, quantity: int) -> float:
    """Charge for an order, rounded up to the centavo (minimum ₱0.01)."""
    return max(math.ceil(round(per_1k * quantity / 1000 * 100, 6)) / 100, 0.01)


SERVICE_SELECT = """
    select s.id, s.platform, s.category, s.auto, s.sort, s.name, s.tier, s.description, s.start_time, s.speed,
           s.drop_risk, s.refill_days, s.markup_pct, s.price_php, s.provider_id, s.provider_service_id,
           ps.rate, ps.min_qty, ps.max_qty, ps.type, ps.name as provider_name, p.currency
      from services s
      join provider_services ps
        on ps.provider_id = s.provider_id and ps.provider_service_id = s.provider_service_id
      join providers p on p.id = s.provider_id
     where s.active and not s.hidden and p.active
"""
