"""In-memory sliding-window limits (one app instance): failed logins, sign-ups, API calls."""
import ipaddress
import os
import time

from fastapi import HTTPException, Request

# Proxies that append to X-Forwarded-For in front of this app: Render, then the
# combined service's nginx. Everything before what Render saw can be made up.
PROXY_HOPS = int(os.environ.get("PROXY_HOPS", "2"))

# https://www.cloudflare.com/ips/ (changes rarely)
_CLOUDFLARE = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)]


def _is_cloudflare(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _CLOUDFLARE)


def client_ip(request: Request) -> str:
    """The visitor's address. What Render saw is trusted; Cloudflare's CF-Connecting-IP only
    when Render saw a Cloudflare address (someone reaching the origin directly can't fake it)."""
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if len(hops) >= PROXY_HOPS:
        seen = hops[-PROXY_HOPS]
    else:
        seen = hops[0] if hops else (request.client.host if request.client else "?")
    if _is_cloudflare(seen):
        return (request.headers.get("cf-connecting-ip") or seen).strip()
    return seen


class Limiter:
    def __init__(self, limit: int, window: float, message: str):
        self.limit, self.window, self.message = limit, window, message
        self.hits: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self.hits.get(key, []) if now - t < self.window]
        if recent:
            self.hits[key] = recent
        else:
            self.hits.pop(key, None)
        if len(self.hits) > 50_000:   # keep memory bounded under a flood of distinct keys
            self.hits.clear()
        return recent

    def check(self, *keys: str) -> None:
        """429 if any key is at its limit (doesn't count this call)."""
        now = time.time()
        for k in keys:
            if len(self._recent(k, now)) >= self.limit:
                raise HTTPException(429, self.message, headers={"Retry-After": str(int(self.window))})

    def add(self, *keys: str) -> None:
        now = time.time()
        for k in keys:
            self.hits.setdefault(k, []).append(now)

    def hit(self, *keys: str) -> None:
        """Count this call, then 429 if over the limit."""
        self.check(*keys)
        self.add(*keys)

    def reset(self, *keys: str) -> None:
        for k in keys:
            self.hits.pop(k, None)
