"""OpenRouter chat call for the Messenger bot: one short JSON answer per customer turn."""
import json
import logging
import re

import httpx

from app.config import get_settings

log = logging.getLogger("bot.llm")
DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct"


class LLMError(Exception):
    pass


def _first_json(text: str) -> dict:
    """The model is asked for JSON only; take the first {...} block in case it adds words around it."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    raise LLMError(f"not JSON: {text[:200]!r}")


async def ask(model: str, system: str, messages: list[dict], max_tokens: int = 160) -> tuple[dict, dict]:
    """Returns (parsed JSON, usage {tokens_in, tokens_out, cost_usd})."""
    s = get_settings()
    if not s.openrouter_api_key:
        raise LLMError("OPENROUTER_API_KEY is not set")
    body = {"model": model or DEFAULT_MODEL, "max_tokens": max_tokens, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, *messages],
            "response_format": {"type": "json_object"},
            "usage": {"include": True}}
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(f"{s.openrouter_api_url}/chat/completions", json=body,
                                headers={"Authorization": f"Bearer {s.openrouter_api_key}",
                                         "HTTP-Referer": "https://smmshiro.com", "X-Title": "SMM Shiro bot"})
    except httpx.HTTPError as e:
        raise LLMError(f"network: {e}") from e
    if r.status_code >= 300:
        raise LLMError(f"{r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"unexpected answer: {str(data)[:200]}")
    u = data.get("usage") or {}
    usage = {"tokens_in": u.get("prompt_tokens"), "tokens_out": u.get("completion_tokens"), "cost_usd": u.get("cost")}
    return _first_json(text), usage
