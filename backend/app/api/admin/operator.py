"""Operator work in the admin: the task queue, picking a reply variant, writing to the
client by hand, taking a conversation over and giving it back to the bot.

All of it is open to the operator role.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import run_approved
from app.admin.access import audit
from app.api.admin.activity import contact_dict, conversation_dict, message_dict
from app.api.admin.common import tenant_row
from app.api.admin.deps import TenantAccess, TenantOperator
from app.api.deps import ServicesDep
from app.db.models import (
    Channel,
    Contact,
    Conversation,
    Message,
    MessageRole,
    OperatorTask,
    TaskKind,
    TaskStatus,
)
from app.errors import AppError, Conflict, NotFound
from app.operator import tasks as ops
from app.operator.tasks import task_dict
from app.receipts.service import decide as decide_receipt
from app.scenarios.outside import lock_run, under_lease
from app.scenarios.store import load_run_definition
from app.services import Services

router = APIRouter(prefix="/v1/admin/tenants/{tenant_id}", tags=["admin-operator"])

MAX_TEXT = 4000


async def _last_client_text(session, conversation_id: uuid.UUID) -> str | None:
    return (
        await session.execute(
            select(Message.text)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.CLIENT,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("/operator/tasks")
async def list_tasks(
    access: TenantOperator,
    services: ServicesDep,
    status: Annotated[TaskStatus, Query()] = TaskStatus.OPEN,
    kind: Annotated[TaskKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    """The queue: oldest open task first (it has waited longest)."""
    async with services.sm() as session:
        query = (
            select(OperatorTask, Conversation, Contact, Channel.title, Channel.kind)
            .join(Conversation, Conversation.id == OperatorTask.conversation_id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .join(Channel, Channel.id == OperatorTask.channel_id)
            .where(OperatorTask.tenant_id == access.tenant_id, OperatorTask.status == status)
        )
        if kind is not None:
            query = query.where(OperatorTask.kind == kind)
        order = (
            OperatorTask.created_at.asc()
            if status == TaskStatus.OPEN
            else OperatorTask.resolved_at.desc()
        )
        rows = (await session.execute(query.order_by(order).limit(limit))).all()
        items = []
        for task, conv, contact, channel_title, channel_kind in rows:
            items.append(
                {
                    **task_dict(task),
                    "conversation": conversation_dict(conv),
                    "contact": contact_dict(contact),
                    "channel": {"title": channel_title, "kind": channel_kind},
                    "last_client_text": await _last_client_text(session, conv.id),
                }
            )
        counts = dict(
            (
                await session.execute(
                    select(OperatorTask.kind, func.count())
                    .where(
                        OperatorTask.tenant_id == access.tenant_id,
                        OperatorTask.status == TaskStatus.OPEN,
                    )
                    .group_by(OperatorTask.kind)
                )
            ).all()
        )
        return {"items": items, "open": {k: int(v) for k, v in counts.items()}}


# what each kind of task allows
ACTIONS: dict[str, set[str]] = {
    TaskKind.CHOOSE_REPLY: {"send", "dismiss"},
    TaskKind.HANDOVER: {"release", "dismiss"},
    # a scenario action waiting for a person: run it / drop it
    TaskKind.APPROVE_ACTION: {"approve", "reject", "dismiss"},
    # a receipt in review: the run waits for the verdict, so no dismiss
    TaskKind.REVIEW_RECEIPT: {"accept", "reject"},
}
# these change a scenario run: under the conversation lease, like a turn
RUN_TASKS = (TaskKind.APPROVE_ACTION, TaskKind.REVIEW_RECEIPT)


class ResolveIn(BaseModel):
    action: Literal["send", "dismiss", "release", "approve", "reject", "accept"]
    variant: int | None = Field(default=None, ge=0, le=10)
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    # review_receipt: why (kept with the receipt, not sent to the client)
    note: str | None = Field(default=None, max_length=1000)
    # the version the operator looked at: variants rebuilt since → outdated
    version: int


@router.post("/operator/tasks/{task_id}/resolve")
async def resolve_task(
    task_id: uuid.UUID, body: ResolveIn, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session:
        found = (
            await session.execute(
                select(OperatorTask.conversation_id, OperatorTask.kind).where(
                    OperatorTask.id == task_id, OperatorTask.tenant_id == access.tenant_id
                )
            )
        ).first()
    if found is None:
        raise NotFound("operator_task.not_found", {"id": str(task_id)})
    conv_id, kind = found
    if body.action not in ACTIONS.get(kind, set()):
        raise AppError("operator_task.action_invalid", {"action": body.action})
    if kind in RUN_TASKS:
        return await under_lease(
            services,
            conv_id,
            f"operator:{task_id}",
            lambda session, conv: _resolve(session, conv, task_id, body, access, services),
        )
    async with services.sm() as session, session.begin():
        # the same lock order as a turn's write: the conversation, then its task
        conv = (
            await session.execute(
                select(Conversation).where(Conversation.id == conv_id).with_for_update()
            )
        ).scalar_one()
        return await _resolve(session, conv, task_id, body, access, services)


async def _resolve(
    session: AsyncSession,
    conv: Conversation,
    task_id: uuid.UUID,
    body: ResolveIn,
    access: TenantAccess,
    services: Services,
) -> dict[str, Any]:
    now = services.clock.now()
    user_id = access.user.id
    task = (
        await session.execute(
            select(OperatorTask)
            .where(OperatorTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if task.status != TaskStatus.OPEN:
        raise Conflict("operator_task.closed", {"status": task.status})
    if task.version != body.version:
        raise Conflict("operator_task.outdated", {"version": task.version})

    result: dict[str, Any] = {"action": body.action}
    message = None
    if body.action == "send":
        variants = list((task.payload or {}).get("variants") or [])
        chosen = None
        if body.variant is not None:
            if body.variant >= len(variants):
                raise AppError("operator_task.variant_invalid", {"variant": body.variant})
            chosen = variants[body.variant]
        text = (body.text if body.text is not None else chosen or "").strip()
        if not text:
            raise AppError("operator_task.text_required")
        edited = chosen is None or text != chosen.strip()
        message = await ops.operator_message(
            session,
            services,
            conv,
            text,
            now=now,
            meta={
                "task_id": str(task.id),
                "variant": body.variant,
                "edited": edited,
                "user_id": str(user_id),
            },
        )
        result.update(variant=body.variant, edited=edited, message_id=str(message.id))
    elif body.action == "release":
        conv.handover = False
        conv.handover_at = None
    elif task.kind == TaskKind.REVIEW_RECEIPT:
        result.update(
            await decide_receipt(
                session,
                services,
                conv,
                task,
                accept=body.action == "accept",
                user_id=user_id,
                note=body.note,
                now=now,
            )
        )
    elif body.action == "approve":
        result.update(await _approve(session, services, task, now))

    # rejecting a receipt is a decision (done); rejecting an action drops it
    dropped = body.action == "dismiss" or (
        body.action == "reject" and task.kind == TaskKind.APPROVE_ACTION
    )
    task.status = TaskStatus.CANCELLED if dropped else TaskStatus.DONE
    task.result = result
    task.resolved_by = user_id
    task.resolved_at = now
    task.updated_at = now
    await audit(
        session,
        access.user,
        "operator_task.resolve",
        tenant_id=access.tenant_id,
        target_type="conversation",
        target_id=conv.id,
        data={"task_id": str(task.id), "kind": task.kind, **result},
    )
    return {
        "task": task_dict(task),
        "message": message_dict(message) if message is not None else None,
        "conversation": conversation_dict(conv),
    }


async def _approve(
    session: AsyncSession, services: Services, task: OperatorTask, now: datetime
) -> dict[str, Any]:
    """Run the scenario action the task holds, as it was when it fired."""
    payload = task.payload or {}
    run = await lock_run(session, uuid.UUID(str(payload.get("run_id"))))
    if run is None:
        return {"ran": False}
    defn = await load_run_definition(session, run)
    ran = await run_approved(session, services, run, defn, payload, now=now)
    return {"ran": ran}


async def _conversation(session, conversation_id: uuid.UUID, tenant_id: uuid.UUID):
    conv = await tenant_row(
        session, Conversation, conversation_id, tenant_id, "conversation.not_found"
    )
    # lock: an operator action and a turn's write never mix
    await session.refresh(conv, with_for_update=True)
    return conv


class OperatorMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


@router.post("/conversations/{conversation_id}/messages")
async def write_to_client(
    conversation_id: uuid.UUID,
    body: OperatorMessageIn,
    access: TenantOperator,
    services: ServicesDep,
) -> dict[str, Any]:
    """An operator writes to the client directly. Closes the open choose_reply task:
    the client got a human answer."""
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        conv = await _conversation(session, conversation_id, access.tenant_id)
        if conv.sandbox:
            raise Conflict("conversation.sandbox")
        message = await ops.operator_message(
            session,
            services,
            conv,
            body.text.strip(),
            now=now,
            meta={"user_id": str(access.user.id)},
        )
        await ops.close_open(
            session,
            conv.id,
            TaskKind.CHOOSE_REPLY,
            result={"action": "answered_manually", "message_id": str(message.id)},
            now=now,
            user_id=access.user.id,
        )
        await audit(
            session,
            access.user,
            "conversation.operator_message",
            tenant_id=access.tenant_id,
            target_type="conversation",
            target_id=conv.id,
            data={"message_id": str(message.id)},
        )
        return {"message": message_dict(message), "conversation": conversation_dict(conv)}


@router.post("/conversations/{conversation_id}/handover")
async def take_over(
    conversation_id: uuid.UUID, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    """An operator takes the conversation: the bot goes quiet until it is released."""
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        conv = await _conversation(session, conversation_id, access.tenant_id)
        if not conv.handover:
            conv.handover = True
            conv.handover_at = now
            text = await _last_client_text(session, conv.id) or ""
            task, _ = await ops.open_handover(session, conv, turn_id=None, text=text, now=now)
            task.payload = {**(task.payload or {}), "taken_by": str(access.user.id)}
            await audit(
                session,
                access.user,
                "conversation.handover",
                tenant_id=access.tenant_id,
                target_type="conversation",
                target_id=conv.id,
            )
        return {"conversation": conversation_dict(conv)}


@router.post("/conversations/{conversation_id}/release")
async def release(
    conversation_id: uuid.UUID, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    """Give the conversation back to the bot: it answers the next client message."""
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        conv = await _conversation(session, conversation_id, access.tenant_id)
        conv.handover = False
        conv.handover_at = None
        closed = await ops.close_open(
            session,
            conv.id,
            TaskKind.HANDOVER,
            result={"action": "released"},
            now=now,
            user_id=access.user.id,
        )
        await audit(
            session,
            access.user,
            "conversation.release",
            tenant_id=access.tenant_id,
            target_type="conversation",
            target_id=conv.id,
            data={"tasks_closed": closed},
        )
        return {"conversation": conversation_dict(conv)}


class ConversationPatchIn(BaseModel):
    operator_mode: bool | None = None


@router.patch("/conversations/{conversation_id}")
async def patch_conversation(
    conversation_id: uuid.UUID,
    body: ConversationPatchIn,
    access: TenantOperator,
    services: ServicesDep,
) -> dict[str, Any]:
    """Operator mode for one conversation (the channel setting covers all of them)."""
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        conv = await _conversation(session, conversation_id, access.tenant_id)
        changes: dict[str, Any] = {}
        if body.operator_mode is not None and body.operator_mode != conv.operator_mode:
            conv.operator_mode = changes["operator_mode"] = body.operator_mode
            if not body.operator_mode:
                # back to automatic replies: the variants nobody picked are not needed
                await ops.close_open(
                    session,
                    conv.id,
                    TaskKind.CHOOSE_REPLY,
                    result={"action": "operator_mode_off"},
                    now=now,
                    user_id=access.user.id,
                    status=TaskStatus.CANCELLED,
                )
        if changes:
            await audit(
                session,
                access.user,
                "conversation.update",
                tenant_id=access.tenant_id,
                target_type="conversation",
                target_id=conv.id,
                data=changes,
            )
        return {"conversation": conversation_dict(conv)}
