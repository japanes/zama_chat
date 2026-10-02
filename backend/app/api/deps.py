"""Shared FastAPI dependencies: Services and the API-key client."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select, update

from app.crypto import sha256_hex
from app.db.models import ApiKey, Channel, ChannelStatus, Tenant
from app.errors import Unauthorized
from app.services import Services

API_KEY_PREFIX = "zk_"
LAST_USED_RESOLUTION = timedelta(minutes=1)


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


@dataclass(frozen=True)
class ApiClient:
    api_key_id: uuid.UUID
    tenant_id: uuid.UUID
    channel_id: uuid.UUID
    channel_kind: str


async def api_client(
    services: ServicesDep,
    authorization: Annotated[str | None, Header()] = None,
) -> ApiClient:
    """`Authorization: Bearer zk_…` → the tenant and the http channel of the key.

    Unknown / revoked key, inactive tenant or disabled channel → 401
    auth.invalid_api_key (one code for all: no hints which part is wrong).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("auth.invalid_api_key")
    raw = authorization[7:].strip()
    if not raw:
        raise Unauthorized("auth.invalid_api_key")
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        row = (
            await session.execute(
                select(ApiKey, Channel, Tenant)
                .join(Channel, Channel.id == ApiKey.channel_id)
                .join(Tenant, Tenant.id == ApiKey.tenant_id)
                .where(ApiKey.key_hash == sha256_hex(raw))
            )
        ).first()
        if row is None:
            raise Unauthorized("auth.invalid_api_key")
        key, channel, tenant = row
        if (
            key.revoked_at is not None
            or not tenant.is_active
            or channel.status == ChannelStatus.DISABLED
            or channel.tenant_id != tenant.id
        ):
            raise Unauthorized("auth.invalid_api_key")
        if key.last_used_at is None or now - key.last_used_at > LAST_USED_RESOLUTION:
            await session.execute(
                update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now)
            )
        return ApiClient(key.id, tenant.id, channel.id, channel.kind)


ApiClientDep = Annotated[ApiClient, Depends(api_client)]
