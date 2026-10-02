"""Operator tasks: open / rebuild / close, and messages written by an operator.

All functions run inside the caller's transaction.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.outbox import queue_outgoing
from app.db.models import (
    Conversation,
    Message,
    MessageRole,
    OperatorTask,
    TaskKind,
    TaskStatus,
)

if TYPE_CHECKING:
    from app.services import Services

MAX_INPUT = 2000


def task_dict(task: OperatorTask) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "conversation_id": str(task.conversation_id),
        "channel_id": str(task.channel_id),
        "turn_id": str(task.turn_id) if task.turn_id else None,
        "kind": task.kind,
        "status": task.status,
        "version": task.version,
        "payload": task.payload or {},
        "result": task.result or {},
        "resolved_by": str(task.resolved_by) if task.resolved_by else None,
        "resolved_at": task.resolved_at.isoformat() if task.resolved_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


async def open_task(
    session: AsyncSession, conversation_id: uuid.UUID, kind: str, *, lock: bool = False
) -> OperatorTask | None:
    stmt = select(OperatorTask).where(
        OperatorTask.conversation_id == conversation_id,
        OperatorTask.kind == kind,
        OperatorTask.status == TaskStatus.OPEN,
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_choose_reply(
    session: AsyncSession,
    conv: Conversation,
    *,
    turn_id: uuid.UUID,
    variants: list[str],
    input_text: str,
    meta: dict[str, Any],
    now: datetime,
) -> tuple[OperatorTask, bool]:
    """Put the variants of this turn into the open choose_reply task of the
    conversation. A new client message rebuilds the variants of the same task
    (version + 1). Returns (task, created)."""
    payload = {
        "variants": variants,
        "input_text": input_text[:MAX_INPUT],
        "built_at": now.isoformat(),
        **meta,
    }
    task = await open_task(session, conv.id, TaskKind.CHOOSE_REPLY, lock=True)
    if task is not None:
        # the client may have written more since the task was opened: keep all of it
        old = (task.payload or {}).get("input_text") or ""
        if old and old != input_text and input_text not in old:
            payload["input_text"] = f"{old}\n{input_text}"[-MAX_INPUT:]
        task.payload = payload
        task.turn_id = turn_id
        task.version += 1
        task.updated_at = now
        return task, False
    task = OperatorTask(
        tenant_id=conv.tenant_id,
        channel_id=conv.channel_id,
        conversation_id=conv.id,
        turn_id=turn_id,
        kind=TaskKind.CHOOSE_REPLY,
        status=TaskStatus.OPEN,
        version=1,
        payload=payload,
        result={},
    )
    session.add(task)
    await session.flush()
    return task, True


async def open_handover(
    session: AsyncSession,
    conv: Conversation,
    *,
    turn_id: uuid.UUID | None,
    text: str,
    now: datetime,
) -> tuple[OperatorTask, bool]:
    task = await open_task(session, conv.id, TaskKind.HANDOVER, lock=True)
    if task is not None:
        payload = dict(task.payload or {})
        payload["last_text"] = text[:MAX_INPUT]
        payload["last_at"] = now.isoformat()
        task.payload = payload
        task.updated_at = now
        return task, False
    task = OperatorTask(
        tenant_id=conv.tenant_id,
        channel_id=conv.channel_id,
        conversation_id=conv.id,
        turn_id=turn_id,
        kind=TaskKind.HANDOVER,
        status=TaskStatus.OPEN,
        version=1,
        payload={"input_text": text[:MAX_INPUT], "last_at": now.isoformat()},
        result={},
    )
    session.add(task)
    await session.flush()
    return task, True


async def close_open(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    kind: str,
    *,
    result: dict[str, Any],
    now: datetime,
    user_id: uuid.UUID | None = None,
    status: TaskStatus = TaskStatus.DONE,
) -> int:
    res = await session.execute(
        update(OperatorTask)
        .where(
            OperatorTask.conversation_id == conversation_id,
            OperatorTask.kind == kind,
            OperatorTask.status == TaskStatus.OPEN,
        )
        .values(
            status=status,
            result=result,
            resolved_by=user_id,
            resolved_at=now,
            updated_at=now,
        )
    )
    return res.rowcount or 0


async def operator_message(
    session: AsyncSession,
    services: "Services",
    conv: Conversation,
    text: str,
    *,
    now: datetime,
    meta: dict[str, Any],
) -> Message:
    """A message to the client from a person."""
    return await queue_outgoing(
        session, services, conv, text, role=MessageRole.OPERATOR, now=now, meta=meta
    )
