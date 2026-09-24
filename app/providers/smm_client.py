"""Client for the standard SMM panel API v2 (SMMGen, SMMFollowom, Peakerr, ...).

All actions are POST form-encoded to one URL with key + action; responses are JSON.
"""
import os

import httpx


class ProviderError(Exception):
    pass


def _chunks(xs, n: int = 100):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


class SMMClient:
    def __init__(self, api_url: str, api_key: str, timeout: float = 30):
        self.api_url, self.api_key = api_url, api_key
        self.timeout = timeout

    @classmethod
    def for_provider(cls, provider: dict) -> "SMMClient":
        key = os.environ.get(provider["api_key_env"], "")
        if not key:
            raise ProviderError(f"env var {provider['api_key_env']} is not set")
        return cls(provider["api_url"], key)

    async def _call(self, **data):
        payload = {"key": self.api_key, **{k: v for k, v in data.items() if v is not None}}
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            r = await http.post(self.api_url, data=payload)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict) and "error" in body:
            raise ProviderError(str(body["error"]))
        return body

    async def services(self) -> list[dict]:
        return await self._call(action="services")

    async def balance(self) -> dict:
        """{"balance": "100.84", "currency": "USD"}"""
        return await self._call(action="balance")

    async def add(self, service: int, link: str, quantity: int, **extra) -> int:
        """extra: runs, interval, comments, ... depending on the service type."""
        body = await self._call(action="add", service=service, link=link, quantity=quantity, **extra)
        return int(body["order"])

    async def statuses(self, order_ids) -> dict[str, dict]:
        """{"123": {"charge", "start_count", "status", "remains", "currency"} | {"error": ...}}"""
        out: dict[str, dict] = {}
        for chunk in _chunks(order_ids):
            out.update(await self._call(action="status", orders=",".join(map(str, chunk))))
        return out

    async def refill(self, order_id: int) -> int:
        return int((await self._call(action="refill", order=order_id))["refill"])

    async def refill_statuses(self, refill_ids) -> list[dict]:
        """[{"refill": 1, "status": "Completed" | "Rejected" | ... | {"error": ...}}]"""
        out: list[dict] = []
        for chunk in _chunks(refill_ids):
            out += await self._call(action="refill_status", refills=",".join(map(str, chunk)))
        return out

    async def cancel(self, order_ids) -> list[dict]:
        return await self._call(action="cancel", orders=",".join(map(str, order_ids)))
