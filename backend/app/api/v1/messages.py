"""Public HTTP channel: POST /v1/messages (one client message → bot reply in the
response) and POST /v1/conversations/history."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ApiClientDep, ServicesDep
from app.core.ingest import save_client_message
from app.core.turn import run_turn
from app.db.models import (
    Contact,
    Conversation,
    Message,
    MessageRole,
    OperatorTask,
    SendStatus,
    TaskKind,
    TaskStatus,
    Turn,
)
from app.errors import AppError, NotFound
from app.services import Services

router = APIRouter(prefix="/v1", tags=["messages"])


class ContactIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=200)


class MessageIn(BaseModel):
    contact: ContactIn
    text: str = Field(min_length=1, max_length=8000)
    # the caller's id of this message: sending it again returns the same reply
    client_message_id: str | None = Field(default=None, min_length=1, max_length=200)


class MessageOut(BaseModel):
    conversation_id: uuid.UUID
    turn_id: uuid.UUID | None
    message_id: uuid.UUID | None  # id of the bot reply
    reply: str | None
    # the conversation is handed over to a person: the bot does not answer
    handover: bool = False
    # operator mode: an operator picks the reply; read it later from the history
    waiting_operator: bool = False


@router.post("/messages", response_model=MessageOut)
async def post_message(body: MessageIn, client: ApiClientDep, services: ServicesDep) -> MessageOut:
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        ingested = await save_client_message(
            session,
            tenant_id=client.tenant_id,
            channel_id=client.channel_id,
            contact_external_id=body.contact.external_id,
            contact_name=body.contact.name,
            contact_username=body.contact.username,
            text=body.text,
            external_id=body.client_message_id,
            now=now,
        )

    # A repeated client_message_id whose turn is running or done finds nothing to take:
    # run_turn waits for the lease (the other turn finishing) and returns None, then
    # the stored reply is read. A message given back by a failed turn is answered anew.
    outcome = await run_turn(
        services,
        ingested.conversation_id,
        owner=f"http:{uuid.uuid4().hex}",
        wait_seconds=services.settings.http_lease_wait_seconds,
        only_message_id=ingested.message_id,
    )
    if outcome is not None:
        return MessageOut(
            conversation_id=outcome.conversation_id,
            turn_id=outcome.turn_id,
            message_id=outcome.reply_message_id,
            reply=outcome.reply_text,
            handover=outcome.handover,
            waiting_operator=outcome.waiting_operator,
        )
    return await _stored_reply(services, ingested.conversation_id, ingested.message_id)


async def _stored_reply(
    services: Services, conversation_id: uuid.UUID, client_message_id: uuid.UUID
) -> MessageOut:
    async with services.sm() as session:
        message = await session.get(Message, client_message_id)
        turn = await session.get(Turn, message.turn_id) if message and message.turn_id else None
        if turn is None:
            # the turn failed and gave the message back; the caller may retry
            raise AppError(
                "turn.not_finished", {"conversation_id": str(conversation_id)}, status=503
            )
        conv = await session.get(Conversation, conversation_id)
        reply = (
            await session.execute(
                select(Message).where(
                    Message.turn_id == turn.id,
                    Message.role == MessageRole.BOT,
                    Message.send_status != SendStatus.DROPPED,
                )
            )
        ).scalar_one_or_none()
        waiting = (
            await session.execute(
                select(OperatorTask.id).where(
                    OperatorTask.turn_id == turn.id,
                    OperatorTask.kind == TaskKind.CHOOSE_REPLY,
                    OperatorTask.status == TaskStatus.OPEN,
                )
            )
        ).first() is not None
    return MessageOut(
        conversation_id=conversation_id,
        turn_id=turn.id,
        message_id=reply.id if reply else None,
        reply=reply.text if reply else None,
        handover=bool(conv and conv.handover),
        waiting_operator=waiting,
    )


class HistoryIn(BaseModel):
    contact: ContactIn | None = None
    conversation_id: uuid.UUID | None = None
    limit: int = Field(default=50, ge=1, le=500)


class HistoryMessage(BaseModel):
    id: uuid.UUID
    role: str
    text: str
    created_at: datetime
    send_status: str | None
    attachments: list[Any] = []


class HistoryOut(BaseModel):
    conversation_id: uuid.UUID | None
    messages: list[HistoryMessage]


@router.post("/conversations/history", response_model=HistoryOut)
async def conversation_history(
    body: HistoryIn, client: ApiClientDep, services: ServicesDep
) -> HistoryOut:
    if body.contact is None and body.conversation_id is None:
        raise AppError("request.contact_or_conversation_required")
    async with services.sm() as session:
        query = select(Conversation).where(Conversation.channel_id == client.channel_id)
        if body.conversation_id is not None:
            query = query.where(Conversation.id == body.conversation_id)
        else:
            assert body.contact is not None
            query = query.join(Contact, Contact.id == Conversation.contact_id).where(
                Contact.external_id == body.contact.external_id
            )
        conv = (await session.execute(query)).scalar_one_or_none()
        if conv is None:
            if body.conversation_id is not None:
                raise NotFound("conversation.not_found")
            return HistoryOut(conversation_id=None, messages=[])
        rows = (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conv.id,
                    Message.role != MessageRole.SYSTEM,
                    Message.send_status.is_distinct_from(SendStatus.DROPPED),
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(body.limit)
            )
        ).scalars()
        messages = [
            HistoryMessage(
                id=m.id,
                role=m.role,
                text=m.text,
                created_at=m.created_at,
                send_status=m.send_status,
                attachments=m.attachments or [],
            )
            for m in rows
        ]
    return HistoryOut(conversation_id=conv.id, messages=list(reversed(messages)))
