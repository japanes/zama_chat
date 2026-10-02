"""Admin emails (the sign-in link) over SMTP, texts in en / uk / ru.

Texts live in app/admin/mail/<language>.json; `make i18n-check` checks that every
language has the same keys. A language without a file falls back to English.
"""

import asyncio
import html
import json
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.settings import Settings

MAIL_DIR = Path(__file__).parent / "mail"
DEFAULT_LANGUAGE = "en"


@dataclass
class Mail:
    to: str
    subject: str
    html: str
    text: str


class Mailer(Protocol):
    async def send(self, mail: Mail) -> None: ...


class SmtpMailer:
    """smtplib in a thread: a few emails a day do not need an async SMTP client."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, mail: Mail) -> None:
        await asyncio.to_thread(self._send, mail)

    def _send(self, mail: Mail) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = mail.subject
        msg["From"] = formataddr((s.smtp_from_name, s.smtp_from or s.smtp_user))
        msg["To"] = mail.to
        msg.set_content(mail.text)
        msg.add_alternative(mail.html, subtype="html")
        password = s.smtp_password.get_secret_value()
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, context=ssl.create_default_context(), timeout=20
            ) as smtp:
                if s.smtp_user:
                    smtp.login(s.smtp_user, password)
                smtp.send_message(msg)
            return
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, password)
            smtp.send_message(msg)


@dataclass
class MemoryMailer:
    """Tests: keeps the emails instead of sending them."""

    sent: list[Mail] = field(default_factory=list)

    async def send(self, mail: Mail) -> None:
        self.sent.append(mail)


def make_mailer(settings: Settings) -> Mailer | None:
    return SmtpMailer(settings) if settings.smtp_host else None


@lru_cache
def mail_texts(language: str) -> dict[str, Any]:
    path = MAIL_DIR / f"{language}.json"
    if not path.is_file():
        path = MAIL_DIR / f"{DEFAULT_LANGUAGE}.json"
    return json.loads(path.read_text("utf-8"))


def mail_languages() -> list[str]:
    return sorted(p.stem for p in MAIL_DIR.glob("*.json"))


def magic_link_mail(to: str, link: str, *, language: str, minutes: int) -> Mail:
    texts = mail_texts(language)["magic_link"]
    body = texts["body"].format(minutes=minutes)
    e = html.escape
    # the button is the brand gradient of the admin (#EC6EAD → #3494E6)
    page = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f8fafc;font-family:Inter,Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="480" cellspacing="0" cellpadding="0"
 style="background:#ffffff;border:1px solid #e2e8f0;border-radius:16px;padding:32px">
<tr><td style="font-size:20px;font-weight:700;color:#0f172a;padding-bottom:12px">
{e(texts["heading"])}</td></tr>
<tr><td style="font-size:15px;line-height:22px;color:#334155;padding-bottom:24px">
{e(body)}</td></tr>
<tr><td align="center" style="padding-bottom:24px">
<a href="{e(link)}" style="display:inline-block;padding:12px 28px;border-radius:8px;color:#ffffff;
font-weight:600;text-decoration:none;background:#3494E6;
background-image:linear-gradient(90deg,#EC6EAD,#3494E6)">{e(texts["button"])}</a></td></tr>
<tr><td style="font-size:12px;line-height:18px;color:#64748b">{e(texts["fallback"])}<br>
<a href="{e(link)}" style="color:#3494E6;word-break:break-all">{e(link)}</a></td></tr>
<tr><td style="font-size:12px;color:#94a3b8;padding-top:16px">{e(texts["ignore"])}</td></tr>
</table></td></tr></table></body></html>"""
    text = f"{texts['heading']}\n\n{body}\n\n{link}\n\n{texts['ignore']}\n"
    return Mail(to=to, subject=texts["subject"], html=page, text=text)
