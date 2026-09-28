"""Email verification with a 6-digit code (typed in, not a link: most customers sign up inside
Facebook's in-app browser, where a link would open another browser that isn't logged in).

Only the free trial needs a verified email. Codes last 15 minutes; one new code per minute and
5 per hour per account; 5 wrong tries and the code is dead. Codes are stored as an HMAC, never plain.
"""
import hashlib
import hmac
import html
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app import mailer
from app.config import get_settings
from app.db import transaction

CODE_TTL = timedelta(minutes=15)
RESEND_GAP = timedelta(seconds=60)
MAX_SENDS_HOUR = 5
MAX_ATTEMPTS = 5


def required() -> bool:
    """Verification is only asked for when email sending is set up."""
    return mailer.enabled()


def _hash(user_id: int, code: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _email(code: str) -> tuple[str, str, str]:
    subject = f"{code} is your SMM Shiro code"
    text = (f"Your SMM Shiro verification code is {code}\n\n"
            "Type it on the dashboard to verify your email and unlock your free trial. "
            "It expires in 15 minutes.\n\nDidn't sign up? You can ignore this email.")
    body = f"""<!doctype html><html><body style="margin:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;color:#111">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:440px;background:#fff;border:1px solid #e2e2e2;border-radius:16px">
<tr><td style="padding:28px 28px 8px;font-size:20px;font-weight:700">SMM Shiro</td></tr>
<tr><td style="padding:8px 28px 0;font-size:15px;line-height:1.5">Your verification code:</td></tr>
<tr><td style="padding:14px 28px"><div style="font-size:34px;font-weight:700;letter-spacing:8px;font-family:'Courier New',monospace;background:#f4f4f4;border-radius:12px;padding:14px 0;text-align:center">{html.escape(code)}</div></td></tr>
<tr><td style="padding:0 28px 24px;font-size:14px;line-height:1.5;color:#555">Type it on the dashboard to verify your email and unlock your free trial. It expires in 15 minutes.<br><br>Didn't sign up? You can ignore this email.</td></tr>
</table></td></tr></table></body></html>"""
    return subject, body, text


async def send_code(user_id: int) -> dict:
    """Email a new code. Raises 409 if already verified, 429 if asked too often, 502 if sending failed."""
    now = datetime.now(timezone.utc)
    async with transaction() as db:
        u = await db.fetch_one("select email, email_verified_at from users where id = :u for update", {"u": user_id})
        if u["email_verified_at"]:
            raise HTTPException(409, "Your email is already verified")
        row = await db.fetch_one("select sent_at, sends_hour, hour_start from email_codes where user_id = :u", {"u": user_id})
        sends = 1
        if row:
            wait = int((row["sent_at"] + RESEND_GAP - now).total_seconds())
            if wait > 0:
                raise HTTPException(429, f"Wait {wait} seconds before asking for another code")
            in_hour = now - row["hour_start"] < timedelta(hours=1)
            if in_hour and row["sends_hour"] >= MAX_SENDS_HOUR:
                raise HTTPException(429, "Too many codes requested. Try again in an hour.")
            sends = row["sends_hour"] + 1 if in_hour else 1
        code = f"{secrets.randbelow(10 ** 6):06d}"
        await db.execute("""
            insert into email_codes (user_id, code_hash, expires_at, attempts, sent_at, sends_hour, hour_start)
            values (:u, :h, :exp, 0, :now, :n, :now)
            on conflict (user_id) do update set code_hash = excluded.code_hash, expires_at = excluded.expires_at,
              attempts = 0, sent_at = excluded.sent_at, sends_hour = :n,
              hour_start = case when :n = 1 then excluded.hour_start else email_codes.hour_start end
        """, {"u": user_id, "h": _hash(user_id, code), "exp": now + CODE_TTL, "now": now, "n": sends})
        subject, body, text = _email(code)
        try:
            await mailer.send(u["email"], subject, body, text)
        except mailer.MailError:
            raise HTTPException(502, "We couldn't send the email right now. Try again in a minute.")
    return {"sent": True, "resend_in": int(RESEND_GAP.total_seconds())}


async def check_code(user_id: int, code: str) -> None:
    """Verify the account if the code matches. A wrong try is counted even though the request fails."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    now = datetime.now(timezone.utc)
    error = None
    async with transaction() as db:
        if await db.fetch_val("select email_verified_at is not null from users where id = :u", {"u": user_id}):
            return
        row = await db.fetch_one("select code_hash, expires_at, attempts from email_codes where user_id = :u for update",
                                 {"u": user_id})
        if not row:
            error = (400, "Ask for a code first")
        elif row["attempts"] >= MAX_ATTEMPTS:
            error = (429, "Too many wrong codes. Ask for a new code.")
        elif row["expires_at"] < now:
            error = (400, "This code expired. Ask for a new one.")
        elif len(code) != 6 or not hmac.compare_digest(row["code_hash"], _hash(user_id, code)):
            await db.execute("update email_codes set attempts = attempts + 1 where user_id = :u", {"u": user_id})
            left = MAX_ATTEMPTS - row["attempts"] - 1
            error = (400, f"Wrong code. {left} tr{'y' if left == 1 else 'ies'} left." if left else
                     "Wrong code. Ask for a new code.")
        else:
            await db.execute("update users set email_verified_at = now() where id = :u", {"u": user_id})
            await db.execute("delete from email_codes where user_id = :u", {"u": user_id})
    if error:
        raise HTTPException(*error)
