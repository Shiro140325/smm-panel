"""In-memory sliding-window limits (one app instance): failed logins, sign-ups, API calls."""
import time

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    # behind Cloudflare/Render the client address arrives in a header
    return (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
            or (request.client.host if request.client else "?")).strip()


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
