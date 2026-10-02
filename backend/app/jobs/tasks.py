"""Job handlers. Each takes a JobContext; `ctx.services` is the process Services."""

import uuid

from app.channels.base import PermanentSendError
from app.core.turn import run_turn
from app.db.models import Channel, Contact, Conversation, Message, SendStatus
from app.errors import Conflict
from app.jobs.worker import Fail, Handler, JobContext, Retry
from app.logging import get_logger
from app.services import Services

log = get_logger(__name__)


async def run_turn_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    conversation_id = uuid.UUID(ctx.payload["conversation_id"])
    try:
        await run_turn(services, conversation_id, owner=f"job:{ctx.id}")
    except Conflict as exc:
        if exc.code == "conversation.busy":
            # another turn is running; it will not see messages saved after its TAKE,
            # so try again shortly
            raise Retry(delay_s=2.0, reason="conversation busy") from exc
        raise


async def send_message_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    message_id = uuid.UUID(ctx.payload["message_id"])
    async with services.sm() as session:
        message = await session.get(Message, message_id)
        if message is None:
            raise Fail(f"message {message_id} not found")
        if message.send_status != SendStatus.PENDING:
            return  # already sent / dropped
        conv = await session.get(Conversation, message.conversation_id)
        channel = await session.get(Channel, message.channel_id)
        assert conv is not None and channel is not None
        contact = await session.get(Contact, conv.contact_id)
        assert contact is not None

    adapter = services.channel_adapter(channel.kind)
    if adapter is None:
        await _set_status(services, message_id, SendStatus.FAILED, f"no adapter for {channel.kind}")
        raise Fail(f"no channel adapter for {channel.kind!r}")
    try:
        result = await adapter.send(channel, contact, message)
    except PermanentSendError as exc:
        await _set_status(services, message_id, SendStatus.FAILED, str(exc))
        log.warning("send.permanent_error", message_id=str(message_id), error=str(exc))
        return
    except Exception as exc:
        if ctx.is_last_attempt:
            await _set_status(
                services, message_id, SendStatus.FAILED, f"{type(exc).__name__}: {exc}"
            )
        raise
    await _set_status(
        services,
        message_id,
        SendStatus.SENT,
        None,
        external_id=result.external_id,
        meta=result.meta,
    )


async def _set_status(
    services: Services,
    message_id: uuid.UUID,
    status: SendStatus,
    error: str | None,
    *,
    external_id: str | None = None,
    meta: dict | None = None,
) -> None:
    async with services.sm() as session, session.begin():
        message = await session.get(Message, message_id, with_for_update=True)
        if message is None:
            return
        message.send_status = status
        message.send_error = error[:1000] if error else None
        if status == SendStatus.SENT:
            message.sent_at = services.clock.now()
            if external_id:
                message.external_id = external_id
            if meta:
                message.meta = {**(message.meta or {}), "send": meta}


HANDLERS: dict[str, Handler] = {
    "run_turn": run_turn_job,
    "send_message": send_message_job,
}
