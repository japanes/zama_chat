"""Test data: tenants with an HTTP channel and API key, LLM models with roles."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import API_KEY_PREFIX
from app.core.tenant_settings import TenantSettings
from app.crypto import new_token, sha256_hex
from app.db.models import ApiKey, Channel, ChannelKind, LlmModel, LlmRole, Tenant
from app.llm.registry import assign_role


@dataclass
class TenantBundle:
    tenant_id: uuid.UUID
    channel_id: uuid.UUID
    api_key: str
    api_key_id: uuid.UUID

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


async def make_tenant(
    sm: async_sessionmaker[AsyncSession],
    *,
    slug: str = "acme",
    title: str = "Acme",
    settings: dict[str, Any] | None = None,
    channel_kind: str = ChannelKind.HTTP,
    channel_settings: dict[str, Any] | None = None,
    account_id: str | None = None,
) -> TenantBundle:
    data = TenantSettings(company_title=title).model_dump(mode="json")
    data.update(settings or {})
    async with sm() as session, session.begin():
        tenant = Tenant(slug=slug, title=title, settings=data)
        session.add(tenant)
        await session.flush()
        channel = Channel(
            tenant_id=tenant.id,
            kind=channel_kind,
            title=f"{channel_kind} channel",
            account_id=account_id,
            settings=channel_settings or {},
        )
        session.add(channel)
        await session.flush()
        key = API_KEY_PREFIX + new_token(32)
        api_key = ApiKey(
            tenant_id=tenant.id,
            channel_id=channel.id,
            title="test",
            key_hash=sha256_hex(key),
            prefix=key[:10],
        )
        session.add(api_key)
        await session.flush()
        return TenantBundle(tenant.id, channel.id, key, api_key.id)


async def make_channel(
    sm: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    kind: str,
    account_id: str | None = None,
    settings: dict[str, Any] | None = None,
) -> uuid.UUID:
    async with sm() as session, session.begin():
        channel = Channel(
            tenant_id=tenant_id,
            kind=kind,
            title=f"{kind} channel",
            account_id=account_id,
            settings=settings or {},
        )
        session.add(channel)
        await session.flush()
        return channel.id


async def make_model(
    sm: async_sessionmaker[AsyncSession],
    name: str = "openai/test-model",
    *,
    roles: tuple[str, ...] = (LlmRole.REPLY,),
    tenant_id: uuid.UUID | None = None,
    fallback: str | None = None,
    price_input: str = "0.10",
    price_output: str = "0.50",
    price_cached_input: str | None = None,
    supports_json_schema: bool = True,
    supports_reasoning: bool = False,
    supports_vision: bool = False,
    reasoning_efforts: list[str] | None = None,
    timeout_s: float | None = None,
    reasoning_effort: str | None = None,
    kind: str = "language",
) -> uuid.UUID:
    """Create the model (or reuse it by name) and assign it to `roles`."""
    async with sm() as session, session.begin():
        model = (
            await session.execute(select(LlmModel).where(LlmModel.name == name))
        ).scalar_one_or_none()
        if model is None:
            model = LlmModel(
                name=name,
                label=name,
                kind=kind,
                price_input=Decimal(price_input),
                price_output=Decimal(price_output),
                price_cached_input=(
                    Decimal(price_cached_input) if price_cached_input is not None else None
                ),
                supports_json_schema=supports_json_schema,
                supports_reasoning=supports_reasoning,
                supports_vision=supports_vision,
                reasoning_efforts=reasoning_efforts or [],
            )
            session.add(model)
            await session.flush()
        fb = None
        if fallback:
            fb = (
                await session.execute(select(LlmModel).where(LlmModel.name == fallback))
            ).scalar_one()
        for role in roles:
            await assign_role(
                session,
                role=role,
                model=model,
                tenant_id=tenant_id,
                fallback=fb,
                timeout_s=timeout_s,
                reasoning_effort=reasoning_effort,
            )
        return model.id
