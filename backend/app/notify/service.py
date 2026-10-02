"""Notifications to the team: Telegram and email.

Queued as `notify` jobs in the caller's transaction (one job per chat / address), so
nothing goes out before the commit and a failed chat does not resend to the others.
System events (`handover`, `choose_reply`, `channel_error`) go to the notifiers that
subscribed to them; scenario actions name a notifier directly.
"""

import html
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.mailer import Mail
from app.db.models import Channel, Contact, Conversation, Notifier, NotifierKind, Tenant
from app.errors import AppError
from app.jobs.queue import enqueue
from app.jobs.worker import Fail, JobContext
from app.logging import get_logger
from app.notify.telegram import TelegramError, send_message
from app.notify.texts import render

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

MAX_QUOTE = 500


def targets(notifier: Notifier) -> list[str]:
    config = notifier.config or {}
    key = "chat_ids" if notifier.kind == NotifierKind.TELEGRAM else "to"
    return [str(t).strip() for t in config.get(key) or [] if str(t).strip()]


async def queue(
    session: AsyncSession,
    notifier: Notifier,
    template: str,
    values: dict[str, Any],
    *,
    now: datetime,
) -> int:
    count = 0
    for target in targets(notifier):
        await enqueue(
            session,
            "notify",
            {
                "notifier_id": str(notifier.id),
                "target": target,
                "template": template,
                "values": values,
            },
            run_at=now,
            tenant_id=notifier.tenant_id,
        )
        count += 1
    return count


async def notify_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event: str,
    values: dict[str, Any],
    now: datetime,
) -> int:
    """Queue `event` to every active notifier of the tenant subscribed to it."""
    rows = (
        await session.execute(
            select(Notifier).where(
                Notifier.tenant_id == tenant_id,
                Notifier.is_active.is_(True),
                Notifier.events.contains([event]),
            )
        )
    ).scalars()
    count = 0
    for notifier in rows:
        count += await queue(session, notifier, event, values, now=now)
    return count


async def find_notifier(session: AsyncSession, tenant_id: uuid.UUID, name: str) -> Notifier | None:
    return (
        await session.execute(
            select(Notifier).where(Notifier.tenant_id == tenant_id, Notifier.name == name)
        )
    ).scalar_one_or_none()


def _quote(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= MAX_QUOTE else text[: MAX_QUOTE - 1] + "…"


def contact_label(contact: Contact | None) -> str:
    """«Olha (@olha)»: the name the team calls the client by and the handle to find them."""
    if contact is None:
        return "?"
    handle = f"@{contact.username}" if contact.username else None
    if contact.name and handle:
        return f"{contact.name} ({handle})"
    return contact.name or handle or contact.external_id


async def conversation_values(
    session: AsyncSession, services: "Services", conv: Conversation, *, text: str | None = None
) -> dict[str, Any]:
    """Template values about a conversation: who, where, the link to it in the admin."""
    contact = await session.get(Contact, conv.contact_id)
    channel = await session.get(Channel, conv.channel_id)
    tenant = await session.get(Tenant, conv.tenant_id)
    admin = services.settings.admin_url.rstrip("/")
    slug = tenant.slug if tenant else ""
    return {
        "contact": contact_label(contact),
        "channel": channel.title if channel else "?",
        "text": _quote(text),
        "link": f"{admin}/t/{slug}/conversations/{conv.id}",
    }


class NotifyError(Exception):
    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


async def deliver(
    services: "Services", notifier: Notifier, target: str, template: str, values: dict[str, Any]
) -> None:
    subject, body = render(notifier.language, template, values)
    if notifier.kind == NotifierKind.TELEGRAM:
        if notifier.secret_enc is None:
            raise NotifyError("no bot token", permanent=True)
        try:
            token = services.crypto.decrypt(notifier.secret_enc)
        except AppError as exc:
            raise NotifyError(exc.code, permanent=True) from None
        try:
            await send_message(
                services.http, services.settings.telegram_api_url, token, target, body
            )
        except TelegramError as exc:
            raise NotifyError(str(exc), permanent=exc.permanent) from None
        return
    if notifier.kind == NotifierKind.EMAIL:
        mailer = services.extra.get("mailer")
        if mailer is None:
            raise NotifyError("SMTP is not configured", permanent=True)
        text = f"{body}\n"
        page = (
            '<div style="font-family:Arial,sans-serif;font-size:14px;white-space:pre-line">'
            f"{html.escape(body)}</div>"
        )
        try:
            await mailer.send(Mail(to=target, subject=subject, html=page, text=text))
        except Exception as exc:
            raise NotifyError(type(exc).__name__, permanent=False) from None
        return
    raise NotifyError(f"unknown notifier kind {notifier.kind!r}", permanent=True)


async def notify_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    p = ctx.payload
    async with services.sm() as session:
        notifier = await session.get(Notifier, uuid.UUID(p["notifier_id"]))
    if notifier is None or not notifier.is_active:
        return
    try:
        await deliver(services, notifier, p["target"], p["template"], p.get("values") or {})
    except NotifyError as exc:
        log.warning(
            "notify.failed",
            notifier=notifier.name,
            template=p["template"],
            error=str(exc),
            permanent=exc.permanent,
        )
        if exc.permanent:
            raise Fail(str(exc)) from None
        raise
