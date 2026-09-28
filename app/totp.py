"""Authenticator-app codes (TOTP, RFC 6238: 6 digits, 30-second steps, SHA-1) for the admin panel."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

import segno

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    n = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return str(n % 10 ** DIGITS).zfill(DIGITS)


def now_step(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // STEP)


def check(secret: str, code: str, last_step: int | None = None) -> int | None:
    """The time step the code belongs to (±1 step for clock drift), or None. A step at or before
    `last_step` is refused, so a code can't be used twice."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = now_step()
    for s in (step - 1, step, step + 1):
        if (last_step is None or s > last_step) and hmac.compare_digest(_code(secret, s), code):
            return s
    return None


def otpauth_uri(secret: str, account: str = "admin", issuer: str = "SMM Shiro") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#111111", light="#ffffff")
