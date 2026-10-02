"""Incoming client message → contacts / conversations / messages.

The message is saved first and the turn runs after. The same external id twice
(Meta mid, client_message_id) is dropped silently by ON CONFLICT DO NOTHING.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.db.models import Contact, Conversation, Message, MessageRole


@dataclass
class Ingested:
    contact_id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    duplicate: bool  # the same external id was already saved earlier
    turn_id: uuid.UUID | None  # turn of the existing message (duplicates only)


async def upsert_contact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel_id: uuid.UUID,
    external_id: str,
    name: str | None = None,
    username: str | None = None,
) -> Contact:
    stmt = (
        insert(Contact)
        .values(
            id=new_id(),
            tenant_id=tenant_id,
            channel_id=channel_id,
            external_id=external_id,
            name=name,
            username=username,
            profile={},
        )
        .on_conflict_do_nothing(index_elements=["channel_id", "external_id"])
    )
    await session.execute(stmt)
    contact = (
        await session.execute(
            select(Contact).where(
                Contact.channel_id == channel_id, Contact.external_id == external_id
            )
        )
    ).scalar_one()
    if name and contact.name != name:
        contact.name = name
    if username and contact.username != username:
        contact.username = username
    return contact


async def upsert_conversation(
    session: AsyncSession, *, tenant_id: uuid.UUID, channel_id: uuid.UUID, contact_id: uuid.UUID
) -> uuid.UUID:
    stmt = (
        insert(Conversation)
        .values(
            id=new_id(),
            tenant_id=tenant_id,
            channel_id=channel_id,
            contact_id=contact_id,
            state={},
            operator_mode=False,
            handover=False,
            state_version=0,
        )
        .on_conflict_do_nothing(index_elements=["contact_id"])
    )
    await session.execute(stmt)
    return (
        await session.execute(select(Conversation.id).where(Conversation.contact_id == contact_id))
    ).scalar_one()


async def save_client_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel_id: uuid.UUID,
    contact_external_id: str,
    text: str,
    now: datetime,
    external_id: str | None = None,
    contact_name: str | None = None,
    contact_username: str | None = None,
    attachments: list[Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> Ingested:
    contact = await upsert_contact(
        session,
        tenant_id=tenant_id,
        channel_id=channel_id,
        external_id=contact_external_id,
        name=contact_name,
        username=contact_username,
    )
    conversation_id = await upsert_conversation(
        session, tenant_id=tenant_id, channel_id=channel_id, contact_id=contact.id
    )
    message_id = new_id()
    stmt = insert(Message).values(
        id=message_id,
        tenant_id=tenant_id,
        channel_id=channel_id,
        conversation_id=conversation_id,
        role=MessageRole.CLIENT,
        text=text,
        attachments=attachments or [],
        external_id=external_id,
        meta=meta or {},
        created_at=now,
    )
    if external_id is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["channel_id", "external_id"])
    inserted = (await session.execute(stmt.returning(Message.id))).scalar_one_or_none()
    if inserted is None:
        existing = (
            await session.execute(
                select(Message).where(
                    Message.channel_id == channel_id, Message.external_id == external_id
                )
            )
        ).scalar_one()
        return Ingested(contact.id, existing.conversation_id, existing.id, True, existing.turn_id)

    conv = await session.get(Conversation, conversation_id)
    assert conv is not None
    conv.last_client_at = now
    return Ingested(contact.id, conversation_id, message_id, False, None)
