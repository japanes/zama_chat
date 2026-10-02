"""Tenants, API keys and users: the operations both the admin and the `zama` CLI do."""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import API_KEY_PREFIX
from app.core.tenant_settings import TenantSettings
from app.crypto import new_token, sha256_hex
from app.db.models import ApiKey, Channel, ChannelKind, Membership, Tenant, User, UserRole
from app.errors import AppError, Conflict

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


async def create_tenant(
    session: AsyncSession, *, slug: str, title: str, settings: TenantSettings
) -> tuple[Tenant, Channel]:
    """A tenant with its HTTP channel (the API key needs one)."""
    if not SLUG_RE.match(slug):
        raise AppError("tenant.slug_invalid", {"slug": slug})
    settings.validate_rules()
    exists = (
        await session.execute(select(Tenant.id).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if exists is not None:
        raise Conflict("tenant.slug_taken", {"slug": slug})
    tenant = Tenant(slug=slug, title=title, settings=settings.model_dump(mode="json"))
    session.add(tenant)
    await session.flush()
    channel = Channel(tenant_id=tenant.id, kind=ChannelKind.HTTP, title="HTTP API")
    session.add(channel)
    await session.flush()
    return tenant, channel


async def issue_api_key(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel_id: uuid.UUID | None = None,
    title: str = "",
) -> tuple[ApiKey, str]:
    """A key for an HTTP channel of the tenant (default: the first one).
    Returns (row, raw key); only the hash is stored, the raw key is shown once."""
    query = select(Channel).where(Channel.tenant_id == tenant_id, Channel.kind == ChannelKind.HTTP)
    if channel_id is not None:
        query = query.where(Channel.id == channel_id)
    channel = (await session.execute(query.order_by(Channel.created_at))).scalars().first()
    if channel is None:
        raise AppError("channel.http_not_found", {"tenant_id": str(tenant_id)}, status=404)
    raw = API_KEY_PREFIX + new_token(32)
    key = ApiKey(
        tenant_id=tenant_id,
        channel_id=channel.id,
        title=title,
        key_hash=sha256_hex(raw),
        prefix=raw[:10],
    )
    session.add(key)
    await session.flush()
    return key, raw


async def ensure_user(
    session: AsyncSession, email: str, *, name: str | None = None, locale: str | None = None
) -> tuple[User, bool]:
    """The user with this (normalized) email; created when missing. → (user, created)."""
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(email=email, name=name or None, locale=locale)
    session.add(user)
    await session.flush()
    return user, True


async def set_membership(
    session: AsyncSession, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: UserRole
) -> Membership:
    row = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id, Membership.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = Membership(user_id=user_id, tenant_id=tenant_id, role=role.value)
        session.add(row)
    row.role = role.value
    await session.flush()
    return row
