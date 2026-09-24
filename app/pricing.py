import math

from app import fx


def fx_to_php(currency: str) -> float:
    c = (currency or "USD").upper()
    if c == "PHP":
        return 1.0
    if c == "USD":
        return fx.usd_to_php()
    raise ValueError(f"Unsupported provider currency: {currency}")


def price_per_1k_php(rate: float, currency: str, markup_pct: float) -> float:
    """Customer price per 1,000 in PHP, rounded up to the centavo."""
    raw = float(rate) * fx_to_php(currency) * (1 + float(markup_pct) / 100)
    return math.ceil(raw * 100) / 100


def order_price_php(per_1k: float, quantity: int) -> float:
    """Charge for an order, rounded up to the centavo (minimum ₱0.01)."""
    return max(math.ceil(per_1k * quantity / 1000 * 100) / 100, 0.01)


SERVICE_SELECT = """
    select s.id, s.platform, s.name, s.tier, s.description, s.start_time, s.speed,
           s.drop_risk, s.refill_days, s.markup_pct, s.provider_id, s.provider_service_id,
           ps.rate, ps.min_qty, ps.max_qty, ps.type, p.currency
      from services s
      join provider_services ps
        on ps.provider_id = s.provider_id and ps.provider_service_id = s.provider_service_id
      join providers p on p.id = s.provider_id
     where s.active and p.active
"""
