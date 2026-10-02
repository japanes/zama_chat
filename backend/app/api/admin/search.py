"""Catalog, knowledge base and search (stage 5).

- catalog items: list / create / edit / delete, import from JSON or CSV (upsert by
  external_id);
- knowledge sources: list / create / edit / delete;
- search status (what is indexed, the embedding model), re-index, test search.

Every change queues its index job in the same transaction (search/index.py). Operators
may look, admins change.
"""

import csv
import io
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.access import audit
from app.api.admin.common import Page, get_tenant, iso, money, page_params, tenant_row
from app.api.admin.deps import TenantAdmin, TenantOperator
from app.api.deps import ServicesDep
from app.core.tenant_settings import LANG_RE, TenantSettings
from app.db.base import new_id
from app.db.models import CatalogItem, IndexStatus, KnowledgeSource
from app.decisions.engine import DecideContext
from app.errors import AppError, Conflict
from app.search.base import CatalogFilters
from app.search.index import (
    CATALOG,
    KNOWLEDGE,
    clients,
    embed_model,
    queue_item,
    queue_reindex,
    queue_source,
    queue_unindex,
)
from app.search.service import (
    CONTEXT_ITEMS,
    CONTEXT_KNOWLEDGE,
    SearchNotes,
    find_catalog,
    find_knowledge,
    refine,
)

router = APIRouter(prefix="/v1/admin/tenants/{tenant_id}", tags=["admin-search"])

ATTR_KEY = re.compile(r"^[a-z0-9_]{1,50}$")
MAX_ATTRS = 50
MAX_IMPORT = 5000
ATTR_COLUMN = "attr:"
CSV_COLUMNS = (
    "external_id",
    "title",
    "description",
    "category",
    "price",
    "currency",
    "url",
    "image_url",
    "language",
    "is_active",
)


# --- shapes ---------------------------------------------------------------------------


def _index_info(row: CatalogItem | KnowledgeSource) -> dict[str, Any]:
    return {
        "index_status": row.index_status,
        "index_error": row.index_error,
        "index_model": row.index_model,
        "indexed_at": iso(row.indexed_at),
    }


def item_dict(item: CatalogItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "external_id": item.external_id,
        "title": item.title,
        "description": item.description,
        "category": item.category,
        "price": money(item.price),
        "currency": item.currency,
        "url": item.url,
        "image_url": item.image_url,
        "attributes": item.attributes or {},
        "language": item.language,
        "is_active": item.is_active,
        **_index_info(item),
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


def source_dict(source: KnowledgeSource, *, body: bool = True) -> dict[str, Any]:
    out = {
        "id": str(source.id),
        "title": source.title,
        "url": source.url,
        "language": source.language,
        "is_active": source.is_active,
        "chunks_count": source.chunks_count,
        "size": len(source.body or ""),
        **_index_info(source),
        "created_at": iso(source.created_at),
        "updated_at": iso(source.updated_at),
    }
    if body:
        out["body"] = source.body
    return out


def _check_attributes(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > MAX_ATTRS:
        raise ValueError(f"at most {MAX_ATTRS} attributes")
    for key, item in value.items():
        if not ATTR_KEY.match(key):
            raise ValueError(f"attribute name {key!r}: a-z, 0-9, _")
        values = item if isinstance(item, list) else [item]
        for v in values:
            if not isinstance(v, str | int | float | bool) or (isinstance(v, str) and len(v) > 200):
                raise ValueError(f"attribute {key!r}: text up to 200, a number or yes/no")
    return value


def _check_language(value: str | None) -> str | None:
    if value is not None and not LANG_RE.match(value):
        raise ValueError("language code like uk, en, pt-br")
    return value


class ItemFields(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20000)
    category: str | None = Field(default=None, max_length=200)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, max_length=8)
    url: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=1000)
    attributes: dict[str, Any] = Field(default_factory=dict)
    # default: the tenant's first language
    language: str | None = None
    is_active: bool = True

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_attributes(value)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return _check_language(value)

    @field_validator("category", "currency", "url", "image_url")
    @classmethod
    def _blank_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ItemIn(ItemFields):
    external_id: str = Field(min_length=1, max_length=200)


class ItemPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    category: str | None = Field(default=None, max_length=200)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, max_length=8)
    url: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=1000)
    attributes: dict[str, Any] | None = None
    language: str | None = None
    is_active: bool | None = None

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _check_attributes(value)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return _check_language(value)


async def _primary_language(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    tenant = await get_tenant(session, tenant_id)
    return TenantSettings.model_validate(tenant.settings or {}).primary_language


def _fill(item: CatalogItem, data: ItemFields, language: str) -> None:
    item.title = data.title.strip()
    item.description = data.description.strip()
    item.category = data.category
    item.price = data.price
    item.currency = data.currency.upper() if data.currency else None
    item.url = data.url
    item.image_url = data.image_url
    item.attributes = data.attributes
    item.language = data.language or language
    item.is_active = data.is_active


# --- catalog --------------------------------------------------------------------------


@router.get("/catalog")
async def list_catalog(
    access: TenantOperator,
    services: ServicesDep,
    page: Page = Depends(page_params),
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, pattern=r"^(pending|indexed|error|inactive)$"),
) -> dict[str, Any]:
    where = [CatalogItem.tenant_id == access.tenant_id]
    if q:
        like = f"%{q.strip()}%"
        where.append(or_(CatalogItem.title.ilike(like), CatalogItem.external_id.ilike(like)))
    if category:
        where.append(CatalogItem.category == category)
    if status == "inactive":
        where.append(CatalogItem.is_active.is_(False))
    elif status:
        where.append(CatalogItem.index_status == status)
    async with services.sm() as session:
        total = await session.scalar(select(func.count()).select_from(CatalogItem).where(*where))
        rows = (
            await session.execute(
                select(CatalogItem)
                .where(*where)
                .order_by(CatalogItem.category.nulls_last(), CatalogItem.title)
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        items = [item_dict(i) for i in rows]
        categories = (
            await session.execute(
                select(CatalogItem.category, func.count())
                .where(CatalogItem.tenant_id == access.tenant_id)
                .group_by(CatalogItem.category)
                .order_by(CatalogItem.category.nulls_last())
            )
        ).all()
    return {
        "items": items,
        "total": total or 0,
        "categories": [{"name": c, "count": n} for c, n in categories],
    }


@router.post("/catalog")
async def create_item(body: ItemIn, access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        taken = await session.scalar(
            select(CatalogItem.id).where(
                CatalogItem.tenant_id == access.tenant_id,
                CatalogItem.external_id == body.external_id.strip(),
            )
        )
        if taken is not None:
            raise Conflict("catalog.external_id_taken", {"external_id": body.external_id})
        # the id is set here: the index job is queued before the INSERT
        item = CatalogItem(
            id=new_id(), tenant_id=access.tenant_id, external_id=body.external_id.strip()
        )
        _fill(item, body, await _primary_language(session, access.tenant_id))
        item.index_status = IndexStatus.PENDING
        session.add(item)
        await queue_item(session, item, services.clock.now(), force=True)
        await session.flush()
        await audit(
            session,
            access.user,
            "catalog.create",
            tenant_id=access.tenant_id,
            target_type="catalog_item",
            target_id=item.id,
            data={"external_id": item.external_id, "title": item.title},
        )
        return {"item": item_dict(item)}


@router.get("/catalog/{item_id}")
async def get_item(item_id: uuid.UUID, access: TenantOperator, services: ServicesDep) -> dict:
    async with services.sm() as session:
        item = await tenant_row(
            session, CatalogItem, item_id, access.tenant_id, "catalog.not_found"
        )
        return {"item": item_dict(item)}


@router.patch("/catalog/{item_id}")
async def patch_item(
    item_id: uuid.UUID, body: ItemPatchIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        item = await tenant_row(
            session, CatalogItem, item_id, access.tenant_id, "catalog.not_found"
        )
        changes = body.model_dump(exclude_unset=True)
        if "title" in changes and body.title is not None:
            item.title = body.title.strip()
        if "description" in changes:
            item.description = (body.description or "").strip()
        for key in ("category", "url", "image_url"):
            if key in changes:
                value = changes[key]
                setattr(item, key, value.strip() or None if isinstance(value, str) else None)
        if "currency" in changes:
            item.currency = body.currency.strip().upper() or None if body.currency else None
        if "price" in changes:
            item.price = body.price
        if "attributes" in changes:
            item.attributes = body.attributes or {}
        if "language" in changes and body.language:
            item.language = body.language
        if "is_active" in changes and body.is_active is not None:
            item.is_active = body.is_active
        await queue_item(session, item, services.clock.now())
        await audit(
            session,
            access.user,
            "catalog.update",
            tenant_id=access.tenant_id,
            target_type="catalog_item",
            target_id=item.id,
            data={"fields": sorted(changes)},
        )
        # the index job INSERT autoflushed the UPDATE: updated_at is expired, load it
        await session.flush()
        await session.refresh(item)
        return {"item": item_dict(item)}


@router.delete("/catalog/{item_id}")
async def delete_item(item_id: uuid.UUID, access: TenantAdmin, services: ServicesDep) -> dict:
    async with services.sm() as session, session.begin():
        item = await tenant_row(
            session, CatalogItem, item_id, access.tenant_id, "catalog.not_found"
        )
        await session.delete(item)
        await queue_unindex(session, access.tenant_id, services.clock.now(), item_ids=[item_id])
        await audit(
            session,
            access.user,
            "catalog.delete",
            tenant_id=access.tenant_id,
            target_type="catalog_item",
            target_id=item_id,
            data={"external_id": item.external_id},
        )
        return {"ok": True}


class ImportIn(BaseModel):
    items: list[dict[str, Any]] | None = None
    csv: str | None = Field(default=None, max_length=20_000_000)
    # items of the tenant that are not in the import are switched off
    deactivate_missing: bool = False


def _csv_rows(text: str) -> list[dict[str, Any]]:
    """CSV with a header: the CSV_COLUMNS + `attr:<name>` columns for attributes.
    Delimiter , or ; (guessed). A BOM (Excel's "CSV UTF-8") is dropped."""
    text = text.removeprefix("\ufeff")
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = [h.strip() for h in reader.fieldnames or []]
    unknown = [h for h in header if h not in CSV_COLUMNS and not h.startswith(ATTR_COLUMN)]
    if unknown:
        raise AppError("catalog.import_columns", {"columns": unknown[:5]})
    rows = []
    for raw in reader:
        row: dict[str, Any] = {}
        attributes: dict[str, Any] = {}
        for key, value in raw.items():
            if key is None:
                continue
            key = key.strip()
            value = (value or "").strip()
            if key.startswith(ATTR_COLUMN):
                if value:
                    name = key[len(ATTR_COLUMN) :].strip()
                    parts = [p.strip() for p in value.split("|") if p.strip()]
                    attributes[name] = parts if len(parts) > 1 else value
                continue
            if value == "":
                continue
            if key == "is_active":
                row[key] = value.lower() not in ("0", "false", "no", "ні", "нет")
            elif key == "price":
                try:
                    row[key] = Decimal(value.replace(" ", "").replace(",", "."))
                except InvalidOperation:
                    row[key] = value  # pydantic reports it
            else:
                row[key] = value
        row["attributes"] = attributes
        rows.append(row)
    return rows


@router.post("/catalog/import")
async def import_catalog(
    body: ImportIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Upsert by external_id. Everything is checked first: one bad row → nothing is
    saved and the answer names the row (1-based, as in the file)."""
    raw = _csv_rows(body.csv) if body.csv is not None else list(body.items or [])
    if not raw:
        raise AppError("catalog.import_empty")
    if len(raw) > MAX_IMPORT:
        raise AppError("catalog.import_too_big", {"max": MAX_IMPORT, "rows": len(raw)})
    parsed: list[ItemIn] = []
    for n, data in enumerate(raw, start=1):
        try:
            parsed.append(ItemIn.model_validate(data))
        except ValidationError as exc:
            err = exc.errors()[0]
            where = ".".join(str(p) for p in err.get("loc", ()))
            raise AppError(
                "catalog.import_invalid", {"row": n, "field": where, "error": err.get("msg", "")}
            ) from None
    ids = [p.external_id.strip() for p in parsed]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise AppError("catalog.import_duplicate", {"external_ids": dup[:5]})
    now = services.clock.now()
    created = updated = unchanged = deactivated = 0
    async with services.sm() as session, session.begin():
        language = await _primary_language(session, access.tenant_id)
        existing = {
            i.external_id: i
            for i in (
                await session.execute(
                    select(CatalogItem).where(CatalogItem.tenant_id == access.tenant_id)
                )
            ).scalars()
        }
        for data in parsed:
            ext = data.external_id.strip()
            item = existing.get(ext)
            if item is None:
                item = CatalogItem(id=new_id(), tenant_id=access.tenant_id, external_id=ext)
                _fill(item, data, language)
                item.index_status = IndexStatus.PENDING
                session.add(item)
                await queue_item(session, item, now, force=True)
                created += 1
                continue
            before = item.content_hash
            _fill(item, data, language)
            await queue_item(session, item, now)
            if item.content_hash == before:
                unchanged += 1
            else:
                updated += 1
        if body.deactivate_missing:
            seen = set(ids)
            for ext, item in existing.items():
                if ext not in seen and item.is_active:
                    item.is_active = False
                    await queue_item(session, item, now)
                    deactivated += 1
        await audit(
            session,
            access.user,
            "catalog.import",
            tenant_id=access.tenant_id,
            target_type="catalog",
            data={
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "deactivated": deactivated,
            },
        )
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deactivated": deactivated,
    }


# --- knowledge ------------------------------------------------------------------------


class SourceIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=200_000)
    url: str | None = Field(default=None, max_length=1000)
    language: str | None = None
    is_active: bool = True

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return _check_language(value)


class SourcePatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=200_000)
    url: str | None = Field(default=None, max_length=1000)
    language: str | None = None
    is_active: bool | None = None

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return _check_language(value)


@router.get("/knowledge")
async def list_knowledge(
    access: TenantOperator,
    services: ServicesDep,
    page: Page = Depends(page_params),
    q: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    where = [KnowledgeSource.tenant_id == access.tenant_id]
    if q:
        where.append(KnowledgeSource.title.ilike(f"%{q.strip()}%"))
    async with services.sm() as session:
        total = await session.scalar(
            select(func.count()).select_from(KnowledgeSource).where(*where)
        )
        rows = (
            await session.execute(
                select(KnowledgeSource)
                .where(*where)
                .order_by(KnowledgeSource.title)
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return {"items": [source_dict(s, body=False) for s in rows], "total": total or 0}


@router.post("/knowledge")
async def create_source(
    body: SourceIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        source = KnowledgeSource(
            id=new_id(),
            tenant_id=access.tenant_id,
            title=body.title.strip(),
            body=body.body.strip(),
            url=(body.url or "").strip() or None,
            language=body.language or await _primary_language(session, access.tenant_id),
            is_active=body.is_active,
            index_status=IndexStatus.PENDING,
            chunks_count=0,
        )
        session.add(source)
        await queue_source(session, source, services.clock.now(), force=True)
        await session.flush()
        await audit(
            session,
            access.user,
            "knowledge.create",
            tenant_id=access.tenant_id,
            target_type="knowledge_source",
            target_id=source.id,
            data={"title": source.title},
        )
        return {"source": source_dict(source)}


@router.get("/knowledge/{source_id}")
async def get_source(
    source_id: uuid.UUID, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session:
        source = await tenant_row(
            session, KnowledgeSource, source_id, access.tenant_id, "knowledge.not_found"
        )
        return {"source": source_dict(source)}


@router.patch("/knowledge/{source_id}")
async def patch_source(
    source_id: uuid.UUID, body: SourcePatchIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        source = await tenant_row(
            session, KnowledgeSource, source_id, access.tenant_id, "knowledge.not_found"
        )
        changes = body.model_dump(exclude_unset=True)
        if body.title is not None:
            source.title = body.title.strip()
        if body.body is not None:
            source.body = body.body.strip()
        if "url" in changes:
            source.url = (body.url or "").strip() or None
        if body.language:
            source.language = body.language
        if body.is_active is not None:
            source.is_active = body.is_active
        await queue_source(session, source, services.clock.now())
        await audit(
            session,
            access.user,
            "knowledge.update",
            tenant_id=access.tenant_id,
            target_type="knowledge_source",
            target_id=source.id,
            data={"fields": sorted(changes)},
        )
        # the index job INSERT autoflushed the UPDATE: updated_at is expired, load it
        await session.flush()
        await session.refresh(source)
        return {"source": source_dict(source)}


@router.delete("/knowledge/{source_id}")
async def delete_source(
    source_id: uuid.UUID, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        source = await tenant_row(
            session, KnowledgeSource, source_id, access.tenant_id, "knowledge.not_found"
        )
        await session.delete(source)
        await queue_unindex(session, access.tenant_id, services.clock.now(), source_ids=[source_id])
        await audit(
            session,
            access.user,
            "knowledge.delete",
            tenant_id=access.tenant_id,
            target_type="knowledge_source",
            target_id=source_id,
            data={"title": source.title},
        )
        return {"ok": True}


# --- status, re-index, test search ----------------------------------------------------


async def _counts(session: AsyncSession, model: Any, tenant_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await session.execute(
            select(model.index_status, model.is_active, func.count())
            .where(model.tenant_id == tenant_id)
            .group_by(model.index_status, model.is_active)
        )
    ).all()
    out = {"total": 0, "active": 0, "pending": 0, "indexed": 0, "error": 0}
    for status, active, n in rows:
        out["total"] += n
        if active:
            out["active"] += n
        out[status] = out.get(status, 0) + n
    return out


async def _stale_model(
    session: AsyncSession, model: Any, tenant_id: uuid.UUID, embed: str | None
) -> int:
    """Rows indexed with another embedding model than the current one."""
    if embed is None:
        return 0
    return (
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(
                model.tenant_id == tenant_id,
                model.is_active.is_(True),
                model.index_status == IndexStatus.INDEXED,
                or_(model.index_model.is_(None), model.index_model != embed),
            )
        )
        or 0
    )


@router.get("/search/status")
async def search_status(
    access: TenantOperator, services: ServicesDep, ping: bool = False
) -> dict[str, Any]:
    search = clients(services)
    embed = await embed_model(services, access.tenant_id)
    async with services.sm() as session:
        catalog = await _counts(session, CatalogItem, access.tenant_id)
        knowledge = await _counts(session, KnowledgeSource, access.tenant_id)
        catalog["other_model"] = await _stale_model(
            session, CatalogItem, access.tenant_id, embed if search.vectors else None
        )
        knowledge["other_model"] = await _stale_model(
            session, KnowledgeSource, access.tenant_id, embed
        )
    out: dict[str, Any] = {
        "catalog": catalog,
        "knowledge": knowledge,
        "embed_model": embed,
        "words": search.words is not None,
        "vectors": search.vectors is not None,
    }
    if ping:
        out["ping"] = {
            "words": await _ping(search.words),
            "vectors": await _ping(search.vectors),
        }
    return out


async def _ping(index: Any) -> dict[str, Any] | None:
    if index is None:
        return None
    try:
        await index.ping()
    except Exception as exc:  # shown to the admin as is
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True}


class ReindexIn(BaseModel):
    kind: Literal["catalog", "knowledge", "all"] = "all"
    # everything, not only changed rows (e.g. after an index was wiped)
    force: bool = False


@router.post("/search/reindex")
async def reindex(body: ReindexIn, access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        await queue_reindex(
            session, access.tenant_id, services.clock.now(), kind=body.kind, force=body.force
        )
        await audit(
            session,
            access.user,
            "search.reindex",
            tenant_id=access.tenant_id,
            target_type="search",
            data={"kind": body.kind, "force": body.force},
        )
    return {"ok": True}


class TestSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    kind: Literal["catalog", "knowledge", "both"] = "both"
    # also ask the decider (Jev) which candidates fit — costs one request
    refine: bool = True
    language: str | None = None
    category: str | None = None


@router.post("/search/test")
async def test_search(
    body: TestSearchIn, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    """What the bot would find for this (already rewritten) question."""
    notes = SearchNotes()
    catalog = (
        await find_catalog(
            services,
            access.tenant_id,
            body.query,
            filters=CatalogFilters(categories=[body.category] if body.category else []),
            notes=notes,
        )
        if body.kind in (CATALOG, "both")
        else []
    )
    knowledge = (
        await find_knowledge(
            services, access.tenant_id, body.query, language=body.language, notes=notes
        )
        if body.kind in (KNOWLEDGE, "both")
        else []
    )
    out: dict[str, Any] = {
        "catalog": [f.to_dict() for f in catalog],
        "knowledge": [f.to_dict() for f in knowledge],
        "notes": notes.notes,
    }
    if body.refine and (catalog or knowledge):
        kept = await refine(
            services,
            DecideContext(access.tenant_id),
            body.query,
            {"cand": (catalog, CONTEXT_ITEMS), "kb": (knowledge, CONTEXT_KNOWLEDGE)},
        )
        out["refined"] = {
            "catalog": [f.to_dict() for f in kept["cand"]],
            "knowledge": [f.to_dict() for f in kept["kb"]],
        }
        # p("fits") of every candidate, filled by refine()
        out["catalog"] = [f.to_dict() for f in catalog]
        out["knowledge"] = [f.to_dict() for f in knowledge]
    return out
