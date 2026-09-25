import math

from app import fx


def fx_to_php(currency: str) -> float:
    c = (currency or "USD").upper()
    if c == "PHP":
        return 1.0
    if c == "USD":
        return fx.usd_to_php()
    raise ValueError(f"Unsupported provider currency: {currency}")


# Tiered markup for services without a fixed markup_pct: cheap services need a bigger
# percentage to be worth selling at all, expensive ones a smaller one to stay competitive.
MARKUP_BANDS = [(0.05, 300.0), (0.50, 150.0)]   # (USD per 1K below, markup %)
MARKUP_DEFAULT = 60.0


def tiered_markup(rate_usd: float) -> float:
    for limit, pct in MARKUP_BANDS:
        if rate_usd < limit:
            return pct
    return MARKUP_DEFAULT


def price_per_1k_php(rate: float, currency: str, markup_pct: float | None) -> float:
    """Customer price per 1,000 in PHP, rounded up to the centavo."""
    fx_rate = fx_to_php(currency)
    if markup_pct is None:
        rate_usd = float(rate) if (currency or "USD").upper() == "USD" else float(rate) * fx_rate / fx.usd_to_php()
        markup_pct = tiered_markup(rate_usd)
    raw = float(rate) * fx_rate * (1 + float(markup_pct) / 100)
    return math.ceil(round(raw * 100, 6)) / 100   # round first: 46.400000000000006 must not become 46.41


def order_price_php(per_1k: float, quantity: int) -> float:
    """Charge for an order, rounded up to the centavo (minimum ₱0.01)."""
    return max(math.ceil(round(per_1k * quantity / 1000 * 100, 6)) / 100, 0.01)


SERVICE_SELECT = """
    select s.id, s.platform, s.category, s.auto, s.sort, s.name, s.tier, s.description, s.start_time, s.speed,
           s.drop_risk, s.refill_days, s.markup_pct, s.provider_id, s.provider_service_id,
           ps.rate, ps.min_qty, ps.max_qty, ps.type, ps.name as provider_name, p.currency
      from services s
      join provider_services ps
        on ps.provider_id = s.provider_id and ps.provider_service_id = s.provider_service_id
      join providers p on p.id = s.provider_id
     where s.active and not s.hidden and p.active
"""
