"""Small helpers shared by the admin routers: JSON-friendly values, paging, lookups that
also check the row belongs to the tenant from the path."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Channel, Tenant
from app.errors import NotFound

MAX_PAGE = 200


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def sid(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


class Page(BaseModel):
    limit: int = Field(default=50, ge=1, le=MAX_PAGE)
    offset: int = Field(default=0, ge=0)


def page_params(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0)
) -> Page:
    return Page(limit=limit, offset=offset)


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound("tenant.not_found", {"id": str(tenant_id)})
    return tenant


async def tenant_row[T](
    session: AsyncSession, model: type[T], row_id: uuid.UUID, tenant_id: uuid.UUID, code: str
) -> T:
    """The row by id, only if it belongs to the tenant (otherwise 404, never 403: the
    caller must not learn that the id exists elsewhere)."""
    row = await session.get(model, row_id)
    if row is None or row.tenant_id != tenant_id:  # type: ignore[attr-defined]
        raise NotFound(code, {"id": str(row_id)})
    return row


async def get_channel(
    session: AsyncSession, channel_id: uuid.UUID, tenant_id: uuid.UUID
) -> Channel:
    return await tenant_row(session, Channel, channel_id, tenant_id, "channel.not_found")


def channel_dict(c: Channel) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "kind": c.kind,
        "title": c.title,
        "account_id": c.account_id,
        "username": c.username,
        "avatar_url": c.avatar_url,
        "status": c.status,
        "status_error": c.status_error,
        "has_token": c.token_enc is not None,
        "token_expires_at": iso(c.token_expires_at),
        "settings": c.settings or {},
        "created_at": iso(c.created_at),
    }
