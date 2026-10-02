"""Prompts with versions. tenant_id NULL = shared default; a tenant's own active
version wins over the shared one."""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin


class Prompt(IdMixin, CreatedMixin, Base):
    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", "version", postgresql_nulls_not_distinct=True),
        # at most one active version per (tenant, key)
        Index(
            "uq_prompts_active",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text("is_active"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=False)
    author: Mapped[str | None] = mapped_column(String(200))  # email or "seed"
    note: Mapped[str | None] = mapped_column(Text)
