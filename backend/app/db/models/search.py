"""Catalog and knowledge base (stage 5).

The rows here are the source of truth; Elasticsearch (catalog, by words) and Qdrant
(catalog + knowledge, by meaning) are indexes rebuilt from them by jobs (search/index.py).
`content_hash` is the hash of what goes into the index; a row is indexed again only when
it changed or the embedding model changed (`index_model`).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampsMixin


class IndexStatus(StrEnum):
    PENDING = "pending"  # changed, the index job has not run yet
    INDEXED = "indexed"
    ERROR = "error"  # the job gave up; index_error says why


class IndexedMixin:
    content_hash: Mapped[str] = mapped_column(String(64))
    indexed_hash: Mapped[str | None] = mapped_column(String(64))
    # the embedding model (role `embed`) the vectors were made with
    index_model: Mapped[str | None] = mapped_column(String(200))
    index_status: Mapped[str] = mapped_column(String(16), default=IndexStatus.PENDING)
    index_error: Mapped[str | None] = mapped_column(String(500))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogItem(IdMixin, TimestampsMixin, IndexedMixin, Base):
    """A product or a service of the business. `external_id` is the business's own id
    (SKU, service id in the CRM) — imports update items by it. `attributes` are the
    characteristics the search filters on: {"color": "red", "duration_min": 60}."""

    __tablename__ = "catalog_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id"),
        Index("ix_catalog_items_tenant_category", "tenant_id", "category"),
        Index("ix_catalog_items_tenant_index", "tenant_id", "index_status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(200))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    url: Mapped[str | None] = mapped_column(String(1000))
    image_url: Mapped[str | None] = mapped_column(String(1000))
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # the language the item is written in (picks the ES analyzer)
    language: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(default=True)


class KnowledgeSource(IdMixin, TimestampsMixin, IndexedMixin, Base):
    """A text of the knowledge base (delivery terms, FAQ, policies…). It is cut into
    chunks; each chunk is a point in Qdrant."""

    __tablename__ = "knowledge_sources"
    __table_args__ = (Index("ix_knowledge_sources_tenant", "tenant_id", "index_status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1000))
    language: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(default=True)
    chunks_count: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeChunk(Base):
    """A piece of a source as it is in Qdrant. The id is uuid5 of (source, position,
    text hash): the same text gets the same point id, so re-indexing overwrites it."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_source", "source_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
