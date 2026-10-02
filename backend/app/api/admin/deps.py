"""Admin dependencies: the signed-in user (session cookie) and role checks.

Tenant routes have `{tenant_id}` in the path; `TenantOperator` / `TenantAdmin` /
`TenantOwner` check the user's role in that tenant (a superadmin is the owner of all).
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.admin.access import AdminUser
from app.admin.auth import SESSION_COOKIE, resolve_session
from app.api.deps import ServicesDep
from app.db.models import UserRole
from app.errors import Unauthorized


async def admin_user(request: Request, services: ServicesDep) -> AdminUser:
    user = await resolve_session(services, request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        raise Unauthorized("auth.required")
    return user


AdminDep = Annotated[AdminUser, Depends(admin_user)]


async def superadmin(user: AdminDep) -> AdminUser:
    user.require_superadmin()
    return user


SuperDep = Annotated[AdminUser, Depends(superadmin)]


@dataclass(frozen=True)
class TenantAccess:
    user: AdminUser
    tenant_id: uuid.UUID

    @property
    def role(self) -> str:
        role = self.user.role_in(self.tenant_id)
        assert role is not None
        return role


def _tenant_role(need: UserRole):
    async def dep(tenant_id: uuid.UUID, user: AdminDep) -> TenantAccess:
        user.require(tenant_id, need)
        return TenantAccess(user, tenant_id)

    return dep


TenantOperator = Annotated[TenantAccess, Depends(_tenant_role(UserRole.OPERATOR))]
TenantAdmin = Annotated[TenantAccess, Depends(_tenant_role(UserRole.ADMIN))]
TenantOwner = Annotated[TenantAccess, Depends(_tenant_role(UserRole.OWNER))]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
