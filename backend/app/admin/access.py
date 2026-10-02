"""Who the signed-in admin user is and what they may do.

Roles are per tenant: owner > admin > operator (UserRole). A superadmin is the owner of
every tenant and the only one who manages models, shared prompts, tenants and users.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ROLE_RANK, AuditLog, Membership, User, UserRole
from app.errors import Forbidden, NotFound


@dataclass(frozen=True)
class AdminUser:
    id: uuid.UUID
    email: str
    name: str | None
    avatar_url: str | None
    is_superadmin: bool
    locale: str | None
    roles: dict[uuid.UUID, str] = field(default_factory=dict)  # tenant id → role

    @classmethod
    async def load(cls, session: AsyncSession, user: User) -> "AdminUser":
        rows = (
            await session.execute(
                select(Membership.tenant_id, Membership.role).where(Membership.user_id == user.id)
            )
        ).all()
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            is_superadmin=user.is_superadmin,
            locale=user.locale,
            roles={r.tenant_id: r.role for r in rows},
        )

    def role_in(self, tenant_id: uuid.UUID) -> str | None:
        if self.is_superadmin:
            return UserRole.OWNER
        return self.roles.get(tenant_id)

    def can(self, tenant_id: uuid.UUID, need: UserRole) -> bool:
        role = self.role_in(tenant_id)
        return role is not None and ROLE_RANK[UserRole(role)] >= ROLE_RANK[need]

    def require(self, tenant_id: uuid.UUID, need: UserRole) -> None:
        if self.role_in(tenant_id) is None:
            # not a member: the tenant is not shown at all
            raise NotFound("tenant.not_found", {"id": str(tenant_id)})
        if not self.can(tenant_id, need):
            raise Forbidden("auth.forbidden", {"need": need.value})

    def require_superadmin(self) -> None:
        if not self.is_superadmin:
            raise Forbidden("auth.superadmin_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "email": self.email,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "is_superadmin": self.is_superadmin,
            "locale": self.locale,
            "roles": {str(k): v for k, v in self.roles.items()},
        }


async def audit(
    session: AsyncSession,
    user: AdminUser,
    action: str,
    *,
    tenant_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    data: dict[str, Any] | None = None,
) -> None:
    """One line of the audit log, in the same transaction as the change itself."""
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=user.id,
            user_email=user.email,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            data=data or {},
        )
    )
