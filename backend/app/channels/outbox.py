"""Outgoing messages that are not the turn's reply: an operator's answer, a scenario
reminder. Asynchronous channels send them through the send_message job (outbox); the
HTTP channel caller reads them from /v1/conversations/history."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Channel, Conversation, Message, SendStatus
from app.errors import Conflict
from app.jobs.queue import enqueue

if TYPE_CHECKING:
    from app.services import Services


async def queue_outgoing(
    session: AsyncSession,
    services: "Services",
    conv: Conversation,
    text: str,
    *,
    role: str,
    now: datetime,
    meta: dict[str, Any],
    turn_id: uuid.UUID | None = None,
) -> Message:
    """Raises Conflict("conversation.window_closed") when the channel may not write to
    the client now (Instagram: more than 24 hours since the client's last message)."""
    channel = await session.get(Channel, conv.channel_id)
    assert channel is not None
    adapter = services.channel_adapter(channel.kind)
    if adapter is None:
        raise Conflict("channel.no_adapter", {"kind": channel.kind})
    if not adapter.can_send(conv, role, now):
        raise Conflict("conversation.window_closed", {"conversation_id": str(conv.id)})
    synchronous = adapter.synchronous or conv.sandbox
    message = Message(
        tenant_id=conv.tenant_id,
        channel_id=conv.channel_id,
        conversation_id=conv.id,
        role=role,
        text=text,
        turn_id=turn_id,
        send_status=SendStatus.SENT if synchronous else SendStatus.PENDING,
        sent_at=now if synchronous else None,
        meta=meta,
        created_at=now,
    )
    session.add(message)
    await session.flush()
    if not synchronous:
        await enqueue(
            session,
            "send_message",
            {"message_id": str(message.id)},
            run_at=now,
            tenant_id=conv.tenant_id,
            dedupe_key=f"send:{message.id}",
        )
    conv.last_bot_at = now
    return message
