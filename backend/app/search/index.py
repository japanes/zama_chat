"""Index jobs: catalog items → Elasticsearch + Qdrant, knowledge sources → Qdrant.

- `index_catalog_item` / `index_knowledge_source` — one row; skipped when the indexed
  hash and the embedding model are the same as now;
- `reindex_tenant` — queues the rows that need it (all of them with `force`), e.g.
  after the embedding model changed;
- `unindex` — removes deleted rows from the indexes.

The admin API queues them in the transaction that changes the rows (`queue_*`), so an
edit is never left unindexed. A row changed while its job ran keeps `pending`: the edit
queued a new job, which indexes the new text.
"""

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CatalogItem, IndexStatus, KnowledgeChunk, KnowledgeSource, LlmRole
from app.errors import AppError
from app.jobs.queue import enqueue
from app.jobs.worker import Fail, JobContext, Retry
from app.llm.openai_client import LLMRequestError, LLMTransportError
from app.logging import get_logger
from app.search.base import (
    CatalogDoc,
    Point,
    SearchBadRequest,
    SearchClients,
    SearchUnavailable,
)
from app.search.text import chunk_id, item_hash, item_text, source_chunks, source_hash

if TYPE_CHECKING:
    from datetime import datetime

    from app.services import Services

log = get_logger(__name__)

CATALOG = "catalog"
KNOWLEDGE = "knowledge"
EMBED_BATCH = 64
INDEX_ATTEMPTS = 6

# error codes kept in index_error (the admin translates them)
ERR_VECTORS_OFF = "search.vectors_off"
ERR_EMBED_UNASSIGNED = "search.embed_unassigned"


def clients(services: "Services") -> SearchClients:
    return services.extra.get("search") or SearchClients()


async def embed_model(services: "Services", tenant_id: uuid.UUID) -> str | None:
    """The model of the `embed` role for the tenant; None when no model is assigned."""
    try:
        config = await services.llm.role_config(LlmRole.EMBED, tenant_id)
    except AppError as exc:
        if exc.code in ("llm.role_not_assigned", "llm.model_inactive"):
            return None
        raise
    return config.model.name


async def embed_texts(
    services: "Services", tenant_id: uuid.UUID, texts: Sequence[str]
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch, _ = await services.llm.embed(
            list(texts[start : start + EMBED_BATCH]), tenant_id=tenant_id
        )
        vectors.extend(batch)
    return vectors


# --- queueing (called inside the admin transaction) -----------------------------------


async def queue_item(
    session: AsyncSession, item: CatalogItem, now: "datetime", *, force: bool = False
) -> None:
    item.content_hash = item_hash(item)
    if (
        not force
        and item.indexed_hash == item.content_hash
        and item.index_status == IndexStatus.INDEXED
    ):
        return
    item.index_status = IndexStatus.PENDING
    item.index_error = None
    await enqueue(
        session,
        "index_catalog_item",
        {"item_id": str(item.id)},
        run_at=now,
        tenant_id=item.tenant_id,
        dedupe_key=f"index:item:{item.id}",
        max_attempts=INDEX_ATTEMPTS,
    )


async def queue_source(
    session: AsyncSession, source: KnowledgeSource, now: "datetime", *, force: bool = False
) -> None:
    source.content_hash = source_hash(source)
    if (
        not force
        and source.indexed_hash == source.content_hash
        and source.index_status == IndexStatus.INDEXED
    ):
        return
    source.index_status = IndexStatus.PENDING
    source.index_error = None
    await enqueue(
        session,
        "index_knowledge_source",
        {"source_id": str(source.id)},
        run_at=now,
        tenant_id=source.tenant_id,
        dedupe_key=f"index:source:{source.id}",
        max_attempts=INDEX_ATTEMPTS,
    )


async def queue_unindex(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    now: "datetime",
    *,
    item_ids: Sequence[uuid.UUID] = (),
    source_ids: Sequence[uuid.UUID] = (),
) -> None:
    if not item_ids and not source_ids:
        return
    await enqueue(
        session,
        "unindex",
        {
            "tenant_id": str(tenant_id),
            "item_ids": [str(i) for i in item_ids],
            "source_ids": [str(i) for i in source_ids],
        },
        run_at=now,
        tenant_id=tenant_id,
        max_attempts=INDEX_ATTEMPTS,
    )


async def queue_reindex(
    session: AsyncSession, tenant_id: uuid.UUID, now: "datetime", *, kind: str, force: bool
) -> None:
    await enqueue(
        session,
        "reindex_tenant",
        {"tenant_id": str(tenant_id), "kind": kind, "force": force},
        run_at=now,
        tenant_id=tenant_id,
        dedupe_key=f"reindex:{tenant_id}:{kind}",
        on_conflict="keep",
    )


# --- jobs -----------------------------------------------------------------------------


async def _mark(
    services: "Services",
    model: type[CatalogItem] | type[KnowledgeSource],
    row_id: uuid.UUID,
    *,
    indexed_hash: str | None,
    index_model: str | None,
    error: str | None = None,
    chunks: list[KnowledgeChunk] | None = None,
) -> None:
    """Save the outcome. `indexed_hash` is the hash the job indexed; when the row changed
    meanwhile, it stays pending (its own job is queued)."""
    async with services.sm() as session, session.begin():
        row = await session.get(model, row_id, with_for_update=True)
        if row is None:
            return
        if error is not None:
            row.index_status = IndexStatus.ERROR
            row.index_error = error[:500]
            return
        row.indexed_hash = indexed_hash
        row.index_model = index_model
        row.index_error = None
        row.indexed_at = services.clock.now()
        if row.content_hash == indexed_hash:
            row.index_status = IndexStatus.INDEXED
        if chunks is not None and isinstance(row, KnowledgeSource):
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == row.id))
            session.add_all(chunks)
            row.chunks_count = len(chunks)


async def _fail_or_retry(
    ctx: JobContext,
    model: type[CatalogItem] | type[KnowledgeSource],
    row_id: uuid.UUID,
    exc: Exception,
) -> None:
    services: Services = ctx.services
    transient = isinstance(exc, (SearchUnavailable, LLMTransportError))
    if transient and not ctx.is_last_attempt:
        raise Retry(reason=str(exc)[:200]) from exc
    await _mark(services, model, row_id, indexed_hash=None, index_model=None, error=str(exc))
    raise Fail(str(exc)[:300]) from exc


async def index_catalog_item_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    item_id = uuid.UUID(ctx.payload["item_id"])
    async with services.sm() as session:
        item = await session.get(CatalogItem, item_id)
    if item is None:
        return  # deleted; `unindex` takes it out of the indexes
    search = clients(services)
    current = item_hash(item)
    model = await embed_model(services, item.tenant_id) if search.vectors else None
    if (
        item.indexed_hash == current
        and item.index_model == model
        and item.index_status == IndexStatus.INDEXED
    ):
        return
    try:
        if not item.is_active:
            # switched off: out of the indexes, but the row stays
            if search.words:
                await search.words.delete([item.id])
            if search.vectors and model:
                await search.vectors.delete(search.collection(CATALOG, model), [item.id])
        else:
            text = item_text(item)
            if search.words:
                await search.words.upsert(
                    [
                        CatalogDoc(
                            id=item.id,
                            tenant_id=item.tenant_id,
                            external_id=item.external_id,
                            title=item.title,
                            text=text,
                            language=item.language,
                            category=item.category,
                            price=item.price,
                            attributes=item.attributes or {},
                        )
                    ]
                )
            if search.vectors and model:
                [vector] = await embed_texts(services, item.tenant_id, [text])
                await search.vectors.upsert(
                    search.collection(CATALOG, model),
                    [
                        Point(
                            id=item.id,
                            vector=vector,
                            payload={
                                "tenant_id": str(item.tenant_id),
                                "language": item.language,
                                "ref": item.external_id,
                                "category": item.category or "",
                            },
                        )
                    ],
                )
    except (SearchUnavailable, SearchBadRequest, LLMTransportError, LLMRequestError) as exc:
        await _fail_or_retry(ctx, CatalogItem, item_id, exc)
        return
    await _mark(services, CatalogItem, item_id, indexed_hash=current, index_model=model)


async def index_knowledge_source_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    source_id = uuid.UUID(ctx.payload["source_id"])
    async with services.sm() as session:
        source = await session.get(KnowledgeSource, source_id)
        if source is None:
            return
        old_ids = list(
            (
                await session.execute(
                    select(KnowledgeChunk.id).where(KnowledgeChunk.source_id == source_id)
                )
            ).scalars()
        )
    search = clients(services)
    current = source_hash(source)
    if search.vectors is None:
        # the knowledge base is searched by meaning only
        await _mark(
            services,
            KnowledgeSource,
            source_id,
            indexed_hash=None,
            index_model=None,
            error=ERR_VECTORS_OFF,
        )
        raise Fail(ERR_VECTORS_OFF)
    model = await embed_model(services, source.tenant_id)
    if model is None:
        await _mark(
            services,
            KnowledgeSource,
            source_id,
            indexed_hash=None,
            index_model=None,
            error=ERR_EMBED_UNASSIGNED,
        )
        raise Fail(ERR_EMBED_UNASSIGNED)
    if (
        source.indexed_hash == current
        and source.index_model == model
        and source.index_status == IndexStatus.INDEXED
    ):
        return
    collection = search.collection(KNOWLEDGE, model)
    texts = source_chunks(source) if source.is_active else []
    chunks = [
        KnowledgeChunk(
            id=chunk_id(source.id, n, text),
            tenant_id=source.tenant_id,
            source_id=source.id,
            position=n,
            text=text,
            language=source.language,
        )
        for n, text in enumerate(texts)
    ]
    try:
        if chunks:
            vectors = await embed_texts(services, source.tenant_id, texts)
            await search.vectors.upsert(
                collection,
                [
                    Point(
                        id=chunk.id,
                        vector=vector,
                        payload={
                            "tenant_id": str(source.tenant_id),
                            "language": source.language,
                            "source_id": str(source.id),
                            "ref": str(source.id),
                        },
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
        stale = sorted(set(old_ids) - {c.id for c in chunks})
        if stale:
            await search.vectors.delete(collection, stale)
    except (SearchUnavailable, SearchBadRequest, LLMTransportError, LLMRequestError) as exc:
        await _fail_or_retry(ctx, KnowledgeSource, source_id, exc)
        return
    await _mark(
        services,
        KnowledgeSource,
        source_id,
        indexed_hash=current,
        index_model=model,
        chunks=chunks,
    )


async def reindex_tenant_job(ctx: JobContext) -> None:
    """Queue every row of the tenant that needs indexing (all rows with `force`)."""
    services: Services = ctx.services
    tenant_id = uuid.UUID(ctx.payload["tenant_id"])
    kind = ctx.payload.get("kind") or "all"
    force = bool(ctx.payload.get("force"))
    search = clients(services)
    model = await embed_model(services, tenant_id) if search.vectors else None
    now = services.clock.now()
    queued = 0
    async with services.sm() as session, session.begin():
        if kind in (CATALOG, "all"):
            items = (
                await session.execute(select(CatalogItem).where(CatalogItem.tenant_id == tenant_id))
            ).scalars()
            for item in items:
                stale = item.index_model != model or item.index_status != IndexStatus.INDEXED
                if force or stale or item.indexed_hash != item_hash(item):
                    await queue_item(session, item, now, force=True)
                    queued += 1
        if kind in (KNOWLEDGE, "all"):
            sources = (
                await session.execute(
                    select(KnowledgeSource).where(KnowledgeSource.tenant_id == tenant_id)
                )
            ).scalars()
            for source in sources:
                stale = source.index_model != model or source.index_status != IndexStatus.INDEXED
                if force or stale or source.indexed_hash != source_hash(source):
                    await queue_source(session, source, now, force=True)
                    queued += 1
    log.info("search.reindex_queued", tenant_id=str(tenant_id), kind=kind, queued=queued)


async def unindex_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    tenant_id = uuid.UUID(ctx.payload["tenant_id"])
    item_ids = [uuid.UUID(i) for i in ctx.payload.get("item_ids") or []]
    source_ids = [uuid.UUID(i) for i in ctx.payload.get("source_ids") or []]
    search = clients(services)
    model = await embed_model(services, tenant_id) if search.vectors else None
    try:
        if item_ids and search.words:
            await search.words.delete(item_ids)
        if search.vectors and model:
            if item_ids:
                await search.vectors.delete(search.collection(CATALOG, model), item_ids)
            for source_id in source_ids:
                await search.vectors.delete_where(
                    search.collection(KNOWLEDGE, model),
                    {"tenant_id": str(tenant_id), "source_id": str(source_id)},
                )
    except SearchUnavailable as exc:
        if ctx.is_last_attempt:
            raise Fail(str(exc)) from exc
        raise Retry(reason=str(exc)[:200]) from exc
    except SearchBadRequest as exc:
        raise Fail(str(exc)) from exc
    # TODO(review): points made with an earlier embedding model stay in its old
    # collection; drop old collections by hand after a model change (admin → Search)


INDEX_HANDLERS: dict[str, Any] = {
    "index_catalog_item": index_catalog_item_job,
    "index_knowledge_source": index_knowledge_source_job,
    "reindex_tenant": reindex_tenant_job,
    "unindex": unindex_job,
}
