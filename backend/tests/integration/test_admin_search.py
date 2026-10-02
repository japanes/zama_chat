"""Admin API of the catalog and the knowledge base: CRUD, CSV / JSON import with checks,
roles, the index status, re-index and the test search. Indexes are the in-memory fakes."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, CatalogItem, Job, JobStatus, LlmRole
from app.decisions.fake import FakeDecider
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from tests.admin_helpers import admin_headers
from tests.factories import make_model, make_tenant
from tests.search_fakes import fake_search

pytestmark = pytest.mark.db

EMBED = "openai/text-embedding-3-large"
LANGS = {"languages": ["uk", "en"]}

CSV = (
    "external_id;title;category;price;currency;attr:color\n"
    "sku-1;Манікюр класичний;manicure;300;UAH;\n"
    "sku-2;Манікюр з покриттям;manicure;450,50;UAH;red|nude\n"
)


@pytest.fixture
async def env(services):
    words, vectors = fake_search(services)
    await make_model(services.sm, EMBED, roles=(LlmRole.EMBED,), kind="embedding")
    tenant = await make_tenant(services.sm, settings=LANGS)
    tid = tenant.tenant_id
    return SimpleNamespace(
        tid=tid,
        base=f"/v1/admin/tenants/{tid}",
        words=words,
        vectors=vectors,
        adm=await admin_headers(services, "adm@example.com", tenant_id=tid),
        op=await admin_headers(services, "op@example.com", tenant_id=tid, role="operator"),
        worker=Worker(services.sm, services.clock, HANDLERS, services=services),
    )


def _error(resp) -> dict:
    return resp.json()["error"]


async def _actions(services) -> list[str]:
    async with services.sm() as session:
        query = select(AuditLog.action).order_by(AuditLog.created_at, AuditLog.id)
        return list((await session.execute(query)).scalars())


async def _count(services, tid) -> int:
    async with services.sm() as session:
        query = select(func.count()).select_from(CatalogItem).where(CatalogItem.tenant_id == tid)
        return await session.scalar(query)


# --- catalog CRUD -----------------------------------------------------------------------


async def test_catalog_item_crud(client, services, env):
    resp = await client.post(
        f"{env.base}/catalog",
        json={
            "external_id": "sku-1",
            "title": "  Манікюр  ",
            "category": "manicure",
            "price": "450",
            "currency": "uah",
            "attributes": {"duration": 90, "colors": ["red", "nude"]},
        },
        headers=env.adm,
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["item"]
    assert item["title"] == "Манікюр"
    assert item["currency"] == "UAH"
    assert item["language"] == "uk"  # the tenant's first language
    assert Decimal(item["price"]) == Decimal("450")
    assert item["index_status"] == "pending"
    assert item["created_at"] and item["updated_at"]
    item_id = item["id"]

    await env.worker.run_due()
    item = (await client.get(f"{env.base}/catalog/{item_id}", headers=env.adm)).json()["item"]
    assert item["index_status"] == "indexed"
    assert item["index_model"] == EMBED
    assert len(env.words.docs) == 1

    # the external id is unique in the tenant
    resp = await client.post(
        f"{env.base}/catalog", json={"external_id": "sku-1", "title": "Інше"}, headers=env.adm
    )
    assert resp.status_code == 409
    assert _error(resp) == {
        "code": "catalog.external_id_taken",
        "params": {"external_id": "sku-1"},
    }

    resp = await client.patch(
        f"{env.base}/catalog/{item_id}", json={"price": "500"}, headers=env.adm
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["item"]
    assert Decimal(item["price"]) == Decimal("500")
    assert item["index_status"] == "pending"
    assert item["updated_at"]

    listed = (await client.get(f"{env.base}/catalog", params={"q": "анік"}, headers=env.adm)).json()
    assert listed["total"] == 1
    assert listed["categories"] == [{"name": "manicure", "count": 1}]

    resp = await client.delete(f"{env.base}/catalog/{item_id}", headers=env.adm)
    assert resp.status_code == 200
    resp = await client.get(f"{env.base}/catalog/{item_id}", headers=env.adm)
    assert resp.status_code == 404
    assert _error(resp)["code"] == "catalog.not_found"
    await env.worker.run_due()
    assert env.words.docs == {}

    assert [a for a in await _actions(services) if a.startswith("catalog.")] == [
        "catalog.create",
        "catalog.update",
        "catalog.delete",
    ]


async def test_unchanged_patch_queues_nothing(client, services, env):
    item = (
        await client.post(
            f"{env.base}/catalog", json={"external_id": "sku-1", "title": "A"}, headers=env.adm
        )
    ).json()["item"]
    await env.worker.run_due()
    resp = await client.patch(
        f"{env.base}/catalog/{item['id']}", json={"title": "A"}, headers=env.adm
    )
    assert resp.json()["item"]["index_status"] == "indexed"
    assert await env.worker.run_due() == 0


@pytest.mark.parametrize(
    "attributes",
    [{"Bad Key": "x"}, {"color": {"nested": 1}}, {"note": "x" * 201}],
)
async def test_bad_attributes_are_rejected(client, env, attributes):
    resp = await client.post(
        f"{env.base}/catalog",
        json={"external_id": "sku-1", "title": "A", "attributes": attributes},
        headers=env.adm,
    )
    assert resp.status_code == 422
    assert _error(resp)["code"] == "request.invalid"


async def test_operator_reads_but_does_not_edit(client, env):
    assert (await client.get(f"{env.base}/catalog", headers=env.op)).status_code == 200
    assert (await client.get(f"{env.base}/knowledge", headers=env.op)).status_code == 200
    assert (await client.get(f"{env.base}/search/status", headers=env.op)).status_code == 200
    for method, path, body in [
        ("POST", "/catalog", {"external_id": "a", "title": "A"}),
        ("POST", "/catalog/import", {"items": [{"external_id": "a", "title": "A"}]}),
        ("POST", "/knowledge", {"title": "A", "body": "B"}),
        ("POST", "/search/reindex", {"kind": "all"}),
    ]:
        resp = await client.request(method, env.base + path, json=body, headers=env.op)
        assert resp.status_code == 403, (path, resp.text)
        assert _error(resp)["code"] == "auth.forbidden"


async def test_another_tenant_sees_nothing(client, services, env):
    other = await make_tenant(services.sm, slug="other", settings=LANGS)
    item = (
        await client.post(
            f"{env.base}/catalog", json={"external_id": "sku-1", "title": "A"}, headers=env.adm
        )
    ).json()["item"]
    stranger = await admin_headers(services, "x@example.com", tenant_id=other.tenant_id)
    resp = await client.get(f"{env.base}/catalog/{item['id']}", headers=stranger)
    assert resp.status_code == 404


# --- import -----------------------------------------------------------------------------


async def test_import_csv_then_json(client, services, env):
    resp = await client.post(f"{env.base}/catalog/import", json={"csv": CSV}, headers=env.adm)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 2, "updated": 0, "unchanged": 0, "deactivated": 0}
    await env.worker.run_due()
    assert len(env.words.docs) == 2

    async with services.sm() as session:
        sku2 = await session.scalar(select(CatalogItem).where(CatalogItem.external_id == "sku-2"))
    assert sku2.price == Decimal("450.50")
    assert sku2.attributes == {"color": ["red", "nude"]}
    assert sku2.language == "uk"

    # the same file again: nothing to index
    resp = await client.post(f"{env.base}/catalog/import", json={"csv": CSV}, headers=env.adm)
    assert resp.json() == {"created": 0, "updated": 0, "unchanged": 2, "deactivated": 0}
    assert await env.worker.run_due() == 0

    # JSON: one item changed, the other is not in the file → switched off
    resp = await client.post(
        f"{env.base}/catalog/import",
        json={
            "items": [{"external_id": "sku-1", "title": "Манікюр класичний", "price": 350}],
            "deactivate_missing": True,
        },
        headers=env.adm,
    )
    assert resp.json() == {"created": 0, "updated": 1, "unchanged": 0, "deactivated": 1}
    await env.worker.run_due()
    assert {d.external_id for d in env.words.docs.values()} == {"sku-1"}
    assert "catalog.import" in await _actions(services)


async def test_import_csv_with_a_bom(client, services, env):
    resp = await client.post(
        f"{env.base}/catalog/import",
        json={"csv": "﻿external_id,title\nsku-1,A\n"},
        headers=env.adm,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1


@pytest.mark.parametrize(
    ("body", "code", "params"),
    [
        ({"items": []}, "catalog.import_empty", {}),
        ({"csv": "external_id,title\n"}, "catalog.import_empty", {}),
        (
            {"csv": "external_id,title,colour\nsku-1,A,red\n"},
            "catalog.import_columns",
            {"columns": ["colour"]},
        ),
        (
            {"items": [{"external_id": "a", "title": "A"}, {"external_id": "b"}]},
            "catalog.import_invalid",
            {"row": 2, "field": "title", "error": "Field required"},
        ),
        (
            {"csv": "external_id,title,price\nsku-1,A,abc\n"},
            "catalog.import_invalid",
            None,
        ),
        (
            {"items": [{"external_id": "a", "title": "A"}, {"external_id": "a", "title": "B"}]},
            "catalog.import_duplicate",
            {"external_ids": ["a"]},
        ),
    ],
)
async def test_import_errors_save_nothing(client, services, env, body, code, params):
    resp = await client.post(f"{env.base}/catalog/import", json=body, headers=env.adm)
    assert resp.status_code == 400, resp.text
    error = _error(resp)
    assert error["code"] == code
    if params is not None:
        assert error["params"] == params
    assert await _count(services, env.tid) == 0


async def test_import_too_big(client, env, monkeypatch):
    from app.api.admin import search as api

    monkeypatch.setattr(api, "MAX_IMPORT", 2)
    items = [{"external_id": str(n), "title": "A"} for n in range(3)]
    resp = await client.post(f"{env.base}/catalog/import", json={"items": items}, headers=env.adm)
    assert resp.status_code == 400
    assert _error(resp) == {"code": "catalog.import_too_big", "params": {"max": 2, "rows": 3}}


# --- knowledge --------------------------------------------------------------------------


async def test_knowledge_crud(client, services, env):
    resp = await client.post(
        f"{env.base}/knowledge",
        json={"title": "Доставка", "body": "Новою поштою за 1–2 дні.", "url": " "},
        headers=env.adm,
    )
    assert resp.status_code == 200, resp.text
    source = resp.json()["source"]
    assert source["index_status"] == "pending"
    assert source["url"] is None
    assert source["language"] == "uk"
    source_id = source["id"]

    await env.worker.run_due()
    listed = (await client.get(f"{env.base}/knowledge", headers=env.adm)).json()
    [row] = listed["items"]
    assert "body" not in row  # the list is light
    assert row["chunks_count"] == 1
    assert row["index_status"] == "indexed"
    full = (await client.get(f"{env.base}/knowledge/{source_id}", headers=env.adm)).json()
    assert full["source"]["body"] == "Новою поштою за 1–2 дні."

    resp = await client.patch(
        f"{env.base}/knowledge/{source_id}",
        json={"body": "Укрпоштою за 3 дні."},
        headers=env.adm,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"]["index_status"] == "pending"
    await env.worker.run_due()
    [point] = [p for c in env.vectors.collections.values() for p in c.values()]
    assert point.payload["source_id"] == source_id

    resp = await client.delete(f"{env.base}/knowledge/{source_id}", headers=env.adm)
    assert resp.status_code == 200
    await env.worker.run_due()
    assert [p for c in env.vectors.collections.values() for p in c.values()] == []
    resp = await client.get(f"{env.base}/knowledge/{source_id}", headers=env.adm)
    assert _error(resp)["code"] == "knowledge.not_found"
    assert [a for a in await _actions(services) if a.startswith("knowledge.")] == [
        "knowledge.create",
        "knowledge.update",
        "knowledge.delete",
    ]


# --- status, re-index, test search ------------------------------------------------------


async def test_status_counts_and_ping(client, env):
    await client.post(f"{env.base}/catalog/import", json={"csv": CSV}, headers=env.adm)
    status = (await client.get(f"{env.base}/search/status", headers=env.op)).json()
    assert status["catalog"]["pending"] == 2
    assert status["embed_model"] == EMBED
    assert status["words"] is True
    assert status["vectors"] is True
    assert "ping" not in status

    await env.worker.run_due()
    status = (
        await client.get(f"{env.base}/search/status", params={"ping": "true"}, headers=env.op)
    ).json()
    assert status["catalog"]["indexed"] == 2
    assert status["catalog"]["other_model"] == 0
    assert status["ping"] == {"words": {"ok": True}, "vectors": {"ok": True}}


async def test_reindex_queues_one_job(client, services, env):
    for _ in range(2):
        resp = await client.post(
            f"{env.base}/search/reindex", json={"kind": "all", "force": True}, headers=env.adm
        )
        assert resp.status_code == 200
    async with services.sm() as session:
        jobs = list(
            (
                await session.execute(
                    select(Job).where(
                        Job.dedupe_key == f"reindex:{env.tid}:all",
                        Job.status == JobStatus.QUEUED,
                    )
                )
            ).scalars()
        )
    assert len(jobs) == 1
    assert jobs[0].payload == {"tenant_id": str(env.tid), "kind": "all", "force": True}
    assert "search.reindex" in await _actions(services)


async def test_test_search_shows_candidates_and_what_fits(client, services, env):
    await client.post(f"{env.base}/catalog/import", json={"csv": CSV}, headers=env.adm)
    await client.post(
        f"{env.base}/catalog",
        json={"external_id": "sku-3", "title": "Педикюр", "category": "pedicure"},
        headers=env.adm,
    )
    await env.worker.run_due()
    decider = FakeDecider({"cand:*": lambda q, s: "покриттям" in q.instructions})
    services.extra["decider"] = decider

    resp = await client.post(
        f"{env.base}/search/test",
        json={"query": "манікюр", "kind": "catalog"},
        headers=env.op,
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert {c["ref"] for c in out["catalog"]} == {"sku-1", "sku-2", "sku-3"}
    assert all(c["match"] is not None for c in out["catalog"])
    assert [c["ref"] for c in out["refined"]["catalog"]] == ["sku-2"]
    assert out["knowledge"] == []
    assert {n["what"] for n in out["notes"]} >= {"words", "vectors"}

    # without refine: no decider request
    decider.calls.clear()
    out = (
        await client.post(
            f"{env.base}/search/test",
            json={"query": "манікюр", "kind": "catalog", "category": "manicure", "refine": False},
            headers=env.op,
        )
    ).json()
    assert {c["ref"] for c in out["catalog"]} == {"sku-1", "sku-2"}
    assert "refined" not in out
    assert decider.calls == []
