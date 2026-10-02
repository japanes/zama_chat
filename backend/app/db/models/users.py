"""Admin users, their roles in tenants, sign-in links, sessions and the audit log.

Sign-in is passwordless (Google or a link sent by email), and only users that already
exist can sign in: a superadmin is created with `zama create-user --superadmin`, the
others are invited from the admin.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin, TimestampsMixin


class UserRole(StrEnum):
    """Role in one tenant. Each role can do everything the roles after it can."""

    OWNER = "owner"  # + members and their roles
    ADMIN = "admin"  # + scenarios, prompts, endpoints, keys, settings, sandbox
    OPERATOR = "operator"  # sees conversations and results


ROLE_RANK = {UserRole.OPERATOR: 1, UserRole.ADMIN: 2, UserRole.OWNER: 3}


class User(IdMixin, TimestampsMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)  # lower case
    name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    # every tenant, models and shared prompts
    is_superadmin: Mapped[bool] = mapped_column(default=False)
    # admin interface language (en / uk / ru …); not related to the bot's languages
    locale: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(IdMixin, CreatedMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id"),
        Index("ix_memberships_tenant", "tenant_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))


class LoginToken(IdMixin, CreatedMixin, Base):
    """A sign-in link sent by email: only the sha256 of the token is stored, the link
    works once and for `magic_link_minutes`."""

    __tablename__ = "login_tokens"
    __table_args__ = (Index("ix_login_tokens_email_created", "email", "created_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(IdMixin, CreatedMixin, Base):
    """A signed-in browser: the httpOnly cookie holds the token, the table its sha256.
    Every use moves `expires_at` forward (session_days)."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id"),)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip: Mapped[str | None] = mapped_column(String(64))


class AuditLog(IdMixin, CreatedMixin, Base):
    """Who changed what in the admin. The email is kept as text: the entry stays
    readable after the user is removed."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(100))  # prompt.save, model_role.assign …
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
