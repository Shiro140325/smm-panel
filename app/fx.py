"""Live USD→PHP rate, cached in memory and refreshed by the sync loop.

Sources (free, no key): open.er-api.com, then frankfurter.dev (ECB) as backup.
If both fail, the last good rate is kept; before the first success, USD_TO_PHP from env is used.
"""
import logging
import time

import httpx

from app.config import get_settings

log = logging.getLogger("fx")

SOURCES = (
    ("open.er-api.com", "https://open.er-api.com/v6/latest/USD", lambda j: j["rates"]["PHP"]),
    ("frankfurter.dev", "https://api.frankfurter.dev/v1/latest?from=USD&to=PHP", lambda j: j["rates"]["PHP"]),
)
REFRESH_EVERY_SECONDS = 6 * 3600

_rate: float | None = None
_fetched_at: float = 0.0
_source: str = "env"


async def refresh(force: bool = False) -> float:
    """Fetch the live rate if the cached one is older than REFRESH_EVERY_SECONDS."""
    global _rate, _fetched_at, _source
    if not force and _rate is not None and time.monotonic() - _fetched_at < REFRESH_EVERY_SECONDS:
        return _rate
    async with httpx.AsyncClient(timeout=10) as http:
        for name, url, pick in SOURCES:
            try:
                r = await http.get(url)
                r.raise_for_status()
                rate = float(pick(r.json()))
                if not 20 < rate < 200:            # sanity check against garbage responses
                    raise ValueError(f"implausible rate {rate}")
                _rate, _fetched_at, _source = rate, time.monotonic(), name
                log.info("USD→PHP %.4f from %s", rate, name)
                return rate
            except Exception as e:
                log.warning("fx source %s failed: %s", name, e)
    return usd_to_php_raw()


def usd_to_php_raw() -> float:
    """Market rate: live if we have one, else the env fallback."""
    return _rate if _rate is not None else get_settings().usd_to_php


def usd_to_php() -> float:
    """Rate used for pricing: market rate plus a buffer for conversion/top-up fees on your side."""
    return usd_to_php_raw() * (1 + get_settings().fx_buffer_pct / 100)


def status() -> dict:
    return {
        "market_rate": round(usd_to_php_raw(), 4),
        "pricing_rate": round(usd_to_php(), 4),
        "source": _source,
        "age_seconds": int(time.monotonic() - _fetched_at) if _rate is not None else None,
    }
