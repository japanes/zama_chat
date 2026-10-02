"""Instagram jobs: echoes of outgoing messages and token refresh."""

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.channels.health import set_channel_status
from app.channels.instagram.client import InstagramError
from app.channels.instagram.connect import instagram_client, schedule_refresh
from app.core.ingest import upsert_contact, upsert_conversation
from app.db.base import new_id
from app.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    Contact,
    Conversation,
    Message,
    MessageRole,
    SendStatus,
    TaskKind,
)
from app.errors import AppError
from app.jobs.worker import Fail, JobContext, Retry
from app.logging import get_logger
from app.operator.tasks import close_open

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

ECHO_PENDING_RETRY_S = 5.0
REFRESH_RETRY_S = 3600.0
REFRESH_AGAIN = timedelta(hours=12)


async def ig_echo_job(ctx: JobContext) -> None:
    """A copy of a message sent from our account. Ours (the bot / an operator in the
    admin) → nothing to do. Otherwise a person answered right in the Instagram app:
    save it as an operator message and close the open choose_reply task."""
    services: Services = ctx.services
    p = ctx.payload
    mid = p["mid"]
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        channel = await session.get(Channel, uuid.UUID(p["channel_id"]))
        if channel is None:
            return
        ours = await session.execute(
            select(Message.id)
            .where(
                Message.channel_id == channel.id,
                or_(
                    Message.external_id == mid,
                    Message.meta.contains({"send": {"mids": [mid]}}),
                ),
            )
            .limit(1)
        )
        if ours.first() is not None:
            return
        contact = await upsert_contact(
            session,
            tenant_id=channel.tenant_id,
            channel_id=channel.id,
            external_id=p["client_id"],
        )
        conv_id = await upsert_conversation(
            session, tenant_id=channel.tenant_id, channel_id=channel.id, contact_id=contact.id
        )
        sending = await session.execute(
            select(Message.id)
            .where(
                Message.conversation_id == conv_id,
                Message.role != MessageRole.CLIENT,
                Message.send_status == SendStatus.PENDING,
            )
            .limit(1)
        )
        if sending.first() is not None and not ctx.is_last_attempt:
            # our reply may be on its way and its mid not saved yet
            raise Retry(delay_s=ECHO_PENDING_RETRY_S, reason="outgoing message pending")

        at = datetime.fromisoformat(p["at"]) if p.get("at") else now
        message_id = new_id()
        inserted = (
            await session.execute(
                insert(Message)
                .values(
                    id=message_id,
                    tenant_id=channel.tenant_id,
                    channel_id=channel.id,
                    conversation_id=conv_id,
                    role=MessageRole.OPERATOR,
                    text=p.get("text") or "",
                    attachments=p.get("attachments") or [],
                    external_id=mid,
                    send_status=SendStatus.SENT,
                    sent_at=at,
                    meta={"source": "instagram_app"},
                    created_at=at,
                )
                .on_conflict_do_nothing(index_elements=["channel_id", "external_id"])
                .returning(Message.id)
            )
        ).scalar_one_or_none()
        if inserted is None:
            return
        conv = await session.get(Conversation, conv_id, with_for_update=True)
        assert conv is not None
        conv.last_bot_at = max(conv.last_bot_at or at, at)
        # TODO(review): should a reply typed in the Instagram app also hand the
        # conversation over (the bot goes quiet)? Now the bot keeps answering.
        await close_open(
            session,
            conv_id,
            TaskKind.CHOOSE_REPLY,
            result={"action": "answered_outside", "message_id": str(message_id)},
            now=now,
        )
        log.info("instagram.echo_saved", conversation_id=str(conv_id))


async def refresh_ig_token_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    channel_id = uuid.UUID(ctx.payload["channel_id"])
    now = services.clock.now()
    async with services.sm() as session:
        channel = await session.get(Channel, channel_id)
    if (
        channel is None
        or channel.kind != ChannelKind.INSTAGRAM
        or channel.status == ChannelStatus.DISABLED
        or channel.token_enc is None
    ):
        return
    try:
        token = services.crypto.decrypt(channel.token_enc)
    except AppError as exc:
        await set_channel_status(services, channel_id, ChannelStatus.ERROR, exc.code)
        raise Fail(exc.code) from None
    client = instagram_client(services)
    try:
        fresh = await client.refresh(token)
    except InstagramError as exc:
        log.warning("instagram.refresh_failed", channel_id=str(channel_id), error=exc.short())
        if exc.kind == "auth":
            await set_channel_status(
                services, channel_id, ChannelStatus.ERROR, "instagram.token_invalid"
            )
            return
        if not ctx.is_last_attempt:
            raise Retry(delay_s=REFRESH_RETRY_S, reason=exc.short()) from None
        expires = channel.token_expires_at
        if expires is not None and expires <= now:
            await set_channel_status(
                services, channel_id, ChannelStatus.ERROR, "instagram.token_expired"
            )
            return
        await set_channel_status(
            services, channel_id, ChannelStatus.EXPIRING, "instagram.token_expiring"
        )
        async with services.sm() as session, session.begin():
            row = await session.get(Channel, channel_id)
            if row is not None and row.status != ChannelStatus.DISABLED:
                await schedule_refresh(session, row, now, at=now + REFRESH_AGAIN)
        return

    async with services.sm() as session, session.begin():
        row = await session.get(Channel, channel_id, with_for_update=True)
        if row is None or row.status == ChannelStatus.DISABLED or row.token_enc is None:
            return
        row.token_enc = services.crypto.encrypt(fresh.token)
        row.token_expires_at = (
            now + timedelta(seconds=fresh.expires_in) if fresh.expires_in else None
        )
        if row.status in (ChannelStatus.EXPIRING, ChannelStatus.ERROR):
            row.status = ChannelStatus.CONNECTED
            row.status_error = None
        await schedule_refresh(session, row, now)
    log.info("instagram.token_refreshed", channel_id=str(channel_id))


async def ig_profile_job(ctx: JobContext) -> None:
    """Fill the client's name / username, so the admin shows @username, not an IGSID.
    Best effort: Instagram may refuse (privacy, no consent yet) — then nothing."""
    services: Services = ctx.services
    async with services.sm() as session:
        channel = await session.get(Channel, uuid.UUID(ctx.payload["channel_id"]))
        contact = await session.get(Contact, uuid.UUID(ctx.payload["contact_id"]))
    if channel is None or contact is None or channel.token_enc is None or contact.username:
        return
    data: dict = {}
    try:
        token = services.crypto.decrypt(channel.token_enc)
        data = await instagram_client(services).user_profile(token, contact.external_id)
    except (AppError, InstagramError) as exc:
        detail = exc.short() if isinstance(exc, InstagramError) else exc.code
        log.info("instagram.profile_unavailable", contact_id=str(contact.id), error=detail)
    async with services.sm() as session, session.begin():
        row = await session.get(Contact, contact.id, with_for_update=True)
        if row is None:
            return
        # asked once per contact, whatever the answer
        row.profile = {**(row.profile or {}), "ig_checked_at": services.clock.now().isoformat()}
        if data.get("username"):
            row.username = str(data["username"])[:200]
        if data.get("name") and not row.name:
            row.name = str(data["name"])[:200]
