"""Index jobs on in-memory fakes of Elasticsearch and Qdrant (tests/search_fakes.py):
what goes where, unchanged rows are skipped, switched-off rows leave the indexes, the
knowledge base needs vectors and an embedding model, chunks are replaced."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import (
    CatalogItem,
    IndexStatus,
    Job,
    JobStatus,
    KnowledgeChunk,
    KnowledgeSource,
    LlmRole,
)
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from app.search.base import SearchUnavailable
from app.search.index import (
    CATALOG,
    ERR_EMBED_UNASSIGNED,
    ERR_VECTORS_OFF,
    KNOWLEDGE,
    queue_reindex,
    queue_source,
    queue_unindex,
)
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import jobs_like
from tests.search_fakes import add_item, add_source, change_item, fake_search

pytestmark = pytest.mark.db

EMBED = "openai/text-embedding-3-large"
EMBED_SLUG = "openai_text_embedding_3_large"
LANGUAGES = {"languages": ["uk", "en"]}


async def _env(services, *, words=True, vectors=True, embed=True):
    w, v = fake_search(services, words=words, vectors=vectors)
    if embed:
        await make_model(services.sm, EMBED, roles=(LlmRole.EMBED,), kind="embedding")
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    worker = Worker(services.sm, services.clock, HANDLERS, services=services)
    return SimpleNamespace(words=w, vectors=v, bundle=bundle, worker=worker)


@pytest.fixture
async def env(services):
    return await _env(services)


async def get(services, model, row_id):
    async with services.sm() as session:
        return await session.get(model, row_id)


async def chunks_of(services, source_id) -> list[KnowledgeChunk]:
    async with services.sm() as session:
        query = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.position)
        )
        return list((await session.execute(query)).scalars())


def _catalog(env) -> str:
    return f"test_{CATALOG}__{EMBED_SLUG}"


def _knowledge(env) -> str:
    return f"test_{KNOWLEDGE}__{EMBED_SLUG}"


# --- catalog ----------------------------------------------------------------------------


async def test_item_goes_to_words_and_vectors(services, fake_llm, env):
    item = await add_item(
        services,
        env.bundle.tenant_id,
        "sku-1",
        "Манікюр з покриттям",
        category="manicure",
        price=Decimal("450"),
        currency="UAH",
        attributes={"duration": 90},
    )
    await env.worker.run_due()

    doc = env.words.docs[item.id]
    assert doc.tenant_id == env.bundle.tenant_id
    assert doc.external_id == "sku-1"
    assert doc.category == "manicure"
    assert "duration: 90" in doc.text
    [point] = env.vectors.points(_catalog(env))
    assert point.id == item.id
    assert point.payload == {
        "tenant_id": str(env.bundle.tenant_id),
        "language": "uk",
        "ref": "sku-1",
        "category": "manicure",
    }
    assert fake_llm.embed_calls == [(EMBED, [doc.text])]

    row = await get(services, CatalogItem, item.id)
    assert row.index_status == IndexStatus.INDEXED
    assert row.indexed_hash == row.content_hash
    assert row.index_model == EMBED
    assert row.indexed_at == services.clock.now()
    assert row.index_error is None


async def test_unchanged_item_is_not_indexed_again(services, fake_llm, env):
    item = await add_item(services, env.bundle.tenant_id, "sku-1", "Манікюр")
    await env.worker.run_due()
    assert len(fake_llm.embed_calls) == 1

    await change_item(services, item.id)  # nothing changed → no job
    assert await env.worker.run_due() == 0
    assert len(fake_llm.embed_calls) == 1

    await change_item(services, item.id, price=Decimal("500"))
    assert await env.worker.run_due() == 1
    assert len(fake_llm.embed_calls) == 2
    row = await get(services, CatalogItem, item.id)
    assert row.index_status == IndexStatus.INDEXED
    assert row.indexed_hash == row.content_hash


async def test_switched_off_item_leaves_the_indexes_but_stays(services, env):
    item = await add_item(services, env.bundle.tenant_id, "sku-1", "Манікюр")
    await env.worker.run_due()
    assert item.id in env.words.docs

    await change_item(services, item.id, is_active=False)
    await env.worker.run_due()
    assert env.words.docs == {}
    assert env.vectors.points(_catalog(env)) == []
    row = await get(services, CatalogItem, item.id)
    assert row is not None
    assert row.index_status == IndexStatus.INDEXED


async def test_unindex_removes_deleted_items_and_sources(services, env):
    item = await add_item(services, env.bundle.tenant_id, "sku-1", "Манікюр")
    source = await add_source(services, env.bundle.tenant_id, "Доставка", "Новою поштою.")
    await env.worker.run_due()
    assert env.vectors.points(_knowledge(env))

    async with services.sm() as session, session.begin():
        await queue_unindex(
            session,
            env.bundle.tenant_id,
            services.clock.now(),
            item_ids=[item.id],
            source_ids=[source.id],
        )
    await env.worker.run_due()
    assert env.words.docs == {}
    assert env.vectors.points(_catalog(env)) == []
    assert env.vectors.points(_knowledge(env)) == []


async def test_catalog_without_an_embedding_model_uses_words_only(services, fake_llm):
    env = await _env(services, embed=False)
    item = await add_item(services, env.bundle.tenant_id, "sku-1", "Манікюр")
    await env.worker.run_due()
    assert item.id in env.words.docs
    assert env.vectors.collections == {}
    assert fake_llm.embed_calls == []
    row = await get(services, CatalogItem, item.id)
    assert row.index_status == IndexStatus.INDEXED
    assert row.index_model is None


async def test_index_down_retries_the_job(services, env):
    env.words.fail = SearchUnavailable("connection refused")
    item = await add_item(services, env.bundle.tenant_id, "sku-1", "Манікюр")
    await env.worker.run_due()

    [job] = await jobs_like(services.sm, f"index:item:{item.id}")
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.run_at > services.clock.now()
    row = await get(services, CatalogItem, item.id)
    assert row.index_status == IndexStatus.PENDING

    # the index is back: the retry indexes the item
    env.words.fail = None
    await env.worker.run_due(job.run_at)
    row = await get(services, CatalogItem, item.id)
    assert row.index_status == IndexStatus.INDEXED


async def test_reindex_after_an_embedding_model_change(services, fake_llm, env):
    item = await add_item(services, env.bundle.tenant_id, "sku-1", "Манікюр")
    await env.worker.run_due()

    other = "openai/text-embedding-3-small"
    await make_model(services.sm, other, roles=(LlmRole.EMBED,), kind="embedding")
    async with services.sm() as session, session.begin():
        await queue_reindex(
            session, env.bundle.tenant_id, services.clock.now(), kind="all", force=False
        )
    await env.worker.run_due()

    [point] = env.vectors.points(f"test_{CATALOG}__openai_text_embedding_3_small")
    assert point.id == item.id
    row = await get(services, CatalogItem, item.id)
    assert row.index_model == other
    assert row.index_status == IndexStatus.INDEXED
    assert fake_llm.embed_calls[-1][0] == other


# --- knowledge --------------------------------------------------------------------------

LONG_BODY = "\n\n".join(f"Пункт {n}. " + "умови доставки " * 60 for n in range(4))


async def test_source_is_cut_into_chunks(services, fake_llm, env):
    source = await add_source(services, env.bundle.tenant_id, "Доставка", LONG_BODY)
    await env.worker.run_due()

    chunks = await chunks_of(services, source.id)
    assert len(chunks) > 1
    assert [c.position for c in chunks] == list(range(len(chunks)))
    assert all(c.text.startswith("Доставка\n") for c in chunks)
    points = {p.id: p for p in env.vectors.points(_knowledge(env))}
    assert set(points) == {c.id for c in chunks}
    assert all(p.payload["source_id"] == str(source.id) for p in points.values())
    assert all(p.payload["ref"] == str(source.id) for p in points.values())

    row = await get(services, KnowledgeSource, source.id)
    assert row.index_status == IndexStatus.INDEXED
    assert row.chunks_count == len(chunks)
    assert row.index_model == EMBED


async def test_shorter_body_replaces_the_chunks(services, env):
    source = await add_source(services, env.bundle.tenant_id, "Доставка", LONG_BODY)
    await env.worker.run_due()
    old = {c.id for c in await chunks_of(services, source.id)}

    async with services.sm() as session, session.begin():
        row = await session.get(KnowledgeSource, source.id)
        row.body = "Тільки Новою поштою."
        await queue_source(session, row, services.clock.now())
    await env.worker.run_due()

    [chunk] = await chunks_of(services, source.id)
    assert chunk.text == "Доставка\nТільки Новою поштою."
    assert chunk.id not in old
    assert [p.id for p in env.vectors.points(_knowledge(env))] == [chunk.id]
    row = await get(services, KnowledgeSource, source.id)
    assert row.chunks_count == 1


async def test_switched_off_source_has_no_chunks(services, env):
    source = await add_source(services, env.bundle.tenant_id, "Доставка", "Новою поштою.")
    await env.worker.run_due()
    async with services.sm() as session, session.begin():
        row = await session.get(KnowledgeSource, source.id)
        row.is_active = False
        await queue_source(session, row, services.clock.now())
    await env.worker.run_due()

    assert await chunks_of(services, source.id) == []
    assert env.vectors.points(_knowledge(env)) == []
    row = await get(services, KnowledgeSource, source.id)
    assert row.chunks_count == 0


async def test_knowledge_needs_the_vector_store(services):
    env = await _env(services, vectors=False)
    source = await add_source(services, env.bundle.tenant_id, "Доставка", "Новою поштою.")
    await env.worker.run_due()

    row = await get(services, KnowledgeSource, source.id)
    assert row.index_status == IndexStatus.ERROR
    assert row.index_error == ERR_VECTORS_OFF
    [job] = await jobs_like(services.sm, f"index:source:{source.id}")
    assert job.status == JobStatus.FAILED


async def test_knowledge_needs_an_embedding_model(services):
    env = await _env(services, embed=False)
    source = await add_source(services, env.bundle.tenant_id, "Доставка", "Новою поштою.")
    await env.worker.run_due()

    row = await get(services, KnowledgeSource, source.id)
    assert row.index_status == IndexStatus.ERROR
    assert row.index_error == ERR_EMBED_UNASSIGNED
    assert await chunks_of(services, source.id) == []


async def test_jobs_are_deduplicated_per_row(services, env):
    item = await add_item(services, env.bundle.tenant_id, "sku-1", "Манікюр")
    await change_item(services, item.id, price=Decimal("1"))
    await change_item(services, item.id, price=Decimal("2"))
    async with services.sm() as session:
        jobs = list(
            (
                await session.execute(
                    select(Job).where(
                        Job.dedupe_key == f"index:item:{item.id}", Job.status == JobStatus.QUEUED
                    )
                )
            ).scalars()
        )
    assert len(jobs) == 1
    await env.worker.run_due()
    row = await get(services, CatalogItem, item.id)
    assert row.price == Decimal("2")
    assert row.index_status == IndexStatus.INDEXED
