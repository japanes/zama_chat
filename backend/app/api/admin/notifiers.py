"""Notifiers: where the team is told about things — a Telegram bot (chats) or email
addresses. System events (handover, choose_reply, channel_error) go to the notifiers
subscribed to them; scenario actions name a notifier (`params: {notifier: <name>}`).

The bot token is stored encrypted and never shown again.
"""

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.admin.access import audit
from app.api.admin.common import iso, tenant_row
from app.api.admin.deps import TenantAdmin
from app.api.deps import ServicesDep
from app.db.models import NOTIFY_EVENTS, Notifier, NotifierKind
from app.errors import AppError, Conflict
from app.notify.service import NotifyError, deliver, targets
from app.notify.texts import LANGUAGES

router = APIRouter(prefix="/v1/admin/tenants/{tenant_id}", tags=["admin-notifiers"])

NAME = r"^[a-z0-9_][a-z0-9_-]{0,99}$"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CHAT_ID = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{5,64})$")
MAX_TARGETS = 20


def notifier_dict(n: Notifier) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "name": n.name,
        "kind": n.kind,
        "targets": targets(n),
        "events": list(n.events or []),
        "language": n.language,
        "is_active": n.is_active,
        "has_secret": n.secret_enc is not None,
        "created_at": iso(n.created_at),
        "updated_at": iso(n.updated_at),
    }


def _check_targets(kind: str, values: list[str]) -> list[str]:
    clean = [v.strip() for v in values if v.strip()]
    if not clean:
        raise AppError("notifier.targets_required")
    if len(clean) > MAX_TARGETS:
        raise AppError("notifier.too_many_targets", {"max": MAX_TARGETS})
    pattern = CHAT_ID if kind == NotifierKind.TELEGRAM else EMAIL
    bad = [v for v in clean if not pattern.match(v)]
    if bad:
        raise AppError("notifier.target_invalid", {"value": bad[0]})
    return list(dict.fromkeys(v.lower() if kind == NotifierKind.EMAIL else v for v in clean))


def _config(kind: str, values: list[str]) -> dict[str, Any]:
    key = "chat_ids" if kind == NotifierKind.TELEGRAM else "to"
    return {key: values}


def _check_events(value: list[str]) -> list[str]:
    unknown = [e for e in value if e not in NOTIFY_EVENTS]
    if unknown:
        raise ValueError(f"unknown events: {unknown}")
    return list(dict.fromkeys(value))


def _check_language(value: str) -> str:
    if value not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}")
    return value


class NotifierBase(BaseModel):
    events: list[str] = Field(default_factory=list)
    language: str = "en"

    @field_validator("events")
    @classmethod
    def _events(cls, value: list[str]) -> list[str]:
        return _check_events(value)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        return _check_language(value)


class NotifierIn(NotifierBase):
    name: str = Field(pattern=NAME)
    kind: Literal["telegram", "email"]
    targets: list[str]
    # telegram: the bot token from @BotFather
    secret: str | None = Field(default=None, min_length=20, max_length=200)
    is_active: bool = True


@router.get("/notifiers")
async def list_notifiers(access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(Notifier)
                .where(Notifier.tenant_id == access.tenant_id)
                .order_by(Notifier.name)
            )
        ).scalars()
        return {"items": [notifier_dict(n) for n in rows], "events": list(NOTIFY_EVENTS)}


@router.post("/notifiers")
async def create_notifier(
    body: NotifierIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    values = _check_targets(body.kind, body.targets)
    if body.kind == NotifierKind.TELEGRAM and not body.secret:
        raise AppError("notifier.token_required")
    async with services.sm() as session, session.begin():
        exists = (
            await session.execute(
                select(Notifier.id).where(
                    Notifier.tenant_id == access.tenant_id, Notifier.name == body.name
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise Conflict("notifier.name_taken", {"name": body.name})
        row = Notifier(
            tenant_id=access.tenant_id,
            name=body.name,
            kind=body.kind,
            secret_enc=services.crypto.encrypt(body.secret.strip()) if body.secret else None,
            config=_config(body.kind, values),
            events=body.events,
            language=body.language,
            is_active=body.is_active,
        )
        session.add(row)
        await session.flush()
        await audit(
            session,
            access.user,
            "notifier.create",
            tenant_id=access.tenant_id,
            target_type="notifier",
            target_id=row.id,
            data={"name": row.name, "kind": row.kind, "events": row.events},
        )
        return {"notifier": notifier_dict(row)}


class NotifierPatchIn(BaseModel):
    targets: list[str] | None = None
    secret: str | None = Field(default=None, min_length=20, max_length=200)
    events: list[str] | None = None
    language: str | None = None
    is_active: bool | None = None

    @field_validator("events")
    @classmethod
    def _events(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_events(value)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return None if value is None else _check_language(value)


@router.patch("/notifiers/{notifier_id}")
async def patch_notifier(
    notifier_id: uuid.UUID, body: NotifierPatchIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        row = await tenant_row(
            session, Notifier, notifier_id, access.tenant_id, "notifier.not_found"
        )
        changes: dict[str, Any] = {}
        if body.targets is not None:
            values = _check_targets(row.kind, body.targets)
            row.config = _config(row.kind, values)
            changes["targets"] = len(values)
        if body.secret is not None:
            row.secret_enc = services.crypto.encrypt(body.secret.strip())
            changes["secret"] = "set"
        if body.events is not None:
            row.events = changes["events"] = body.events
        if body.language is not None:
            row.language = changes["language"] = body.language
        if body.is_active is not None:
            row.is_active = changes["is_active"] = body.is_active
        await audit(
            session,
            access.user,
            "notifier.update",
            tenant_id=access.tenant_id,
            target_type="notifier",
            target_id=row.id,
            data=changes,
        )
        return {"notifier": notifier_dict(row)}


@router.delete("/notifiers/{notifier_id}")
async def delete_notifier(
    notifier_id: uuid.UUID, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Scenario actions that name it fail loudly until it is created again."""
    async with services.sm() as session, session.begin():
        row = await tenant_row(
            session, Notifier, notifier_id, access.tenant_id, "notifier.not_found"
        )
        await session.delete(row)
        await audit(
            session,
            access.user,
            "notifier.delete",
            tenant_id=access.tenant_id,
            target_type="notifier",
            target_id=notifier_id,
            data={"name": row.name},
        )
        return {"ok": True}


@router.post("/notifiers/{notifier_id}/test")
async def test_notifier(
    notifier_id: uuid.UUID, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Send the test text to every chat / address right now (not through the queue),
    so the admin sees at once what does not work."""
    async with services.sm() as session:
        row = await tenant_row(
            session, Notifier, notifier_id, access.tenant_id, "notifier.not_found"
        )
    results = []
    for target in targets(row):
        try:
            await deliver(services, row, target, "test", {})
            results.append({"target": target, "ok": True})
        except NotifyError as exc:
            results.append({"target": target, "ok": False, "error": str(exc)[:300]})
    return {"results": results}
