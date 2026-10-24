"""Job handlers. Each takes a JobContext; `ctx.services` is the process Services."""

import uuid

from app.channels.base import ChannelAuthError, PermanentSendError
from app.channels.health import set_channel_status
from app.channels.instagram.jobs import ig_echo_job, ig_profile_job, refresh_ig_token_job
from app.core.turn import run_turn
from app.db.models import Channel, ChannelStatus, Contact, Conversation, Message, SendStatus
from app.errors import Conflict
from app.jobs.worker import Fail, Handler, JobContext, Retry
from app.logging import get_logger
from app.notify.service import notify_job
from app.results.delivery import deliver_job
from app.scenarios.answers import scenario_answers_job
from app.scenarios.jobs import expire_run_job, silence_check_job
from app.scenarios.runtime import ANSWERS_JOB
from app.search.index import INDEX_HANDLERS
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
        await _set_status(
            services, message_id, SendStatus.FAILED, "channel.no_adapter", detail=channel.kind
        )
        raise Fail(f"no channel adapter for {channel.kind!r}")
    try:
        result = await adapter.send(channel, contact, message, conv)
    except ChannelAuthError as exc:
        # the token is dead: every message of the channel will fail the same way
        await _set_status(services, message_id, SendStatus.FAILED, exc.code, detail=str(exc))
        await set_channel_status(services, channel.id, ChannelStatus.ERROR, exc.code)
        log.warning("send.auth_error", message_id=str(message_id), code=exc.code)
        return
    except PermanentSendError as exc:
        await _set_status(services, message_id, SendStatus.FAILED, exc.code, detail=str(exc))
        log.warning(
            "send.permanent_error", message_id=str(message_id), code=exc.code, error=str(exc)
        )
        return
    except Exception as exc:
        if ctx.is_last_attempt:
            # send_error is a code the admin translates; the details stay in meta
            await _set_status(
                services,
                message_id,
                SendStatus.FAILED,
                "send.failed",
                detail=f"{type(exc).__name__}: {exc}",
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
    detail: str | None = None,
) -> None:
    async with services.sm() as session, session.begin():
        message = await session.get(Message, message_id, with_for_update=True)
        if message is None:
            return
        message.send_status = status
        message.send_error = error[:1000] if error else None
        if detail:
            message.meta = {**(message.meta or {}), "send_error_detail": detail[:1000]}
        if status == SendStatus.SENT:
            message.sent_at = services.clock.now()
            if external_id:
                message.external_id = external_id
            if meta:
                message.meta = {**(message.meta or {}), "send": meta}


HANDLERS: dict[str, Handler] = {
    "run_turn": run_turn_job,
    "send_message": send_message_job,
    "deliver": deliver_job,
    "silence_check": silence_check_job,
    "expire_run": expire_run_job,
    ANSWERS_JOB: scenario_answers_job,
    "notify": notify_job,
    "ig_echo": ig_echo_job,
    "ig_profile": ig_profile_job,
    "refresh_ig_token": refresh_ig_token_job,
    **INDEX_HANDLERS,
}
