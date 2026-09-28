"""Transactional email through Resend (https://resend.com). Off until RESEND_API_KEY is set."""
import logging

import httpx

from app.config import get_settings

log = logging.getLogger("mailer")


class MailError(Exception):
    pass


def enabled() -> bool:
    return bool(get_settings().resend_api_key)


async def send(to: str, subject: str, html: str, text: str) -> str:
    """Send one email; returns Resend's email id. Raises MailError if it wasn't accepted."""
    s = get_settings()
    if not s.resend_api_key:
        raise MailError("Email sending is not set up")
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(f"{s.resend_api_url}/emails",
                                headers={"Authorization": f"Bearer {s.resend_api_key}"},
                                json={"from": s.email_from, "to": [to], "reply_to": s.email_reply_to,
                                      "subject": subject, "html": html, "text": text})
    except httpx.HTTPError as e:
        log.warning("email to %s failed: %s", to, e)
        raise MailError("Couldn't reach the email service") from e
    if r.status_code >= 300:
        log.warning("email to %s rejected: %s %s", to, r.status_code, r.text[:300])
        raise MailError("The email service didn't accept the email")
    return r.json().get("id", "")
