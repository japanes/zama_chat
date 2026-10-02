"""Models and roles in the admin: catalog, add, check, assign, costs.

The main promise: a model switched on a role answers from the very next turn, with no
restart (the role is read from the DB at the start of every call)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import LlmModel, LlmRole, ModelRole
from app.errors import AppError
from app.llm.gateway_catalog import CatalogModel
from app.llm.openai_client import LLMTransportError
from tests.admin_helpers import admin_headers
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant

pytestmark = pytest.mark.db


class FakeCatalog:
    def __init__(self, *models: CatalogModel) -> None:
        self.models = {m.name: m for m in models}

    async def list_models(self, kind: str | None = None) -> list[CatalogModel]:
        return [m for m in self.models.values() if kind is None or m.kind == kind]

    async def get(self, name: str) -> CatalogModel:
        if name not in self.models:
            raise AppError("models.not_in_catalog", {"name": name}, status=404)
        return self.models[name]


def luna(**over) -> CatalogModel:
    data = {
        "name": "openai/gpt-6-luna",
        "label": "GPT-6 Luna",
        "kind": "language",
        "price_input": Decimal("0.10"),
        "price_cached_input": Decimal("0.01"),
        "price_output": Decimal("0.50"),
        "context_window": 272000,
        "max_output": 32000,
        "supports_json_schema": True,
        "supports_reasoning": True,
        "supports_vision": False,
        "reasoning_efforts": ["low", "medium", "high"],
    }
    data.update(over)
    return CatalogModel(**data)


@pytest.fixture
async def root(services):
    return await admin_headers(services, "root@example.com", superadmin=True)


@pytest.fixture
def catalog(services):
    services.catalog = FakeCatalog(
        luna(),
        luna(name="openai/embed-x", label="Embed", kind="embedding", supports_json_schema=False),
    )
    return services.catalog


async def _model_id(services, name: str):
    async with services.sm() as session:
        return (
            await session.execute(select(LlmModel.id).where(LlmModel.name == name))
        ).scalar_one()


# --- models -------------------------------------------------------------------------------


async def test_add_from_gateway_fills_prices(client, services, root, catalog):
    resp = await client.post("/v1/admin/models", json={"name": "openai/gpt-6-luna"}, headers=root)
    assert resp.status_code == 200, resp.text
    model = resp.json()["model"]
    assert model["price_input"] == "0.10"
    assert model["price_output"] == "0.50"
    assert model["reasoning_efforts"] == ["low", "medium", "high"]

    dup = await client.post("/v1/admin/models", json={"name": "openai/gpt-6-luna"}, headers=root)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "models.exists"


async def test_sent_fields_win_over_the_catalog(client, services, root, catalog):
    resp = await client.post(
        "/v1/admin/models",
        json={"name": "openai/gpt-6-luna", "price_output": "0.40", "label": "Luna"},
        headers=root,
    )
    model = resp.json()["model"]
    assert model["price_output"] == "0.40"
    assert model["label"] == "Luna"
    assert model["price_input"] == "0.10"


async def test_add_by_hand_without_the_catalog(client, services, root):
    resp = await client.post(
        "/v1/admin/models",
        json={
            "name": "openai/custom",
            "from_gateway": False,
            "price_input": "1",
            "price_output": "2",
            "supports_json_schema": True,
        },
        headers=root,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"]["price_input"] == "1"


async def test_catalog_marks_added_models(client, services, root, catalog):
    await client.post("/v1/admin/models", json={"name": "openai/gpt-6-luna"}, headers=root)
    items = (await client.get("/v1/admin/models/catalog", headers=root)).json()["items"]
    added = {i["name"]: i["added"] for i in items}
    assert added == {"openai/gpt-6-luna": True, "openai/embed-x": False}
    only = (await client.get("/v1/admin/models/catalog?kind=embedding", headers=root)).json()
    assert [i["name"] for i in only["items"]] == ["openai/embed-x"]


async def test_model_on_a_role_cannot_be_switched_off_or_deleted(client, services, root):
    model_id = await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    off = await client.patch(
        f"/v1/admin/models/{model_id}", json={"is_active": False}, headers=root
    )
    assert off.status_code == 409
    assert off.json()["error"]["code"] == "models.in_use"
    assert off.json()["error"]["params"]["roles"] == ["reply"]
    gone = await client.delete(f"/v1/admin/models/{model_id}", headers=root)
    assert gone.status_code == 409


async def test_fallback_model_counts_as_in_use(client, services, root):
    await make_model(services.sm, "openai/fb", roles=())
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,), fallback="openai/fb")
    fb_id = await _model_id(services, "openai/fb")
    resp = await client.patch(f"/v1/admin/models/{fb_id}", json={"is_active": False}, headers=root)
    assert resp.json()["error"]["code"] == "models.in_use"


async def test_free_model_can_be_switched_off_and_deleted(client, services, root):
    model_id = await make_model(services.sm, "openai/free", roles=())
    off = await client.patch(
        f"/v1/admin/models/{model_id}", json={"is_active": False}, headers=root
    )
    assert off.status_code == 200
    assert off.json()["model"]["is_active"] is False
    assert (await client.delete(f"/v1/admin/models/{model_id}", headers=root)).status_code == 200


async def test_json_schema_cannot_be_taken_from_a_model_on_rewrite(client, services, root):
    model_id = await make_model(services.sm, "openai/a", roles=(LlmRole.REWRITE,))
    resp = await client.patch(
        f"/v1/admin/models/{model_id}", json={"supports_json_schema": False}, headers=root
    )
    assert resp.json()["error"]["code"] == "models.json_schema_required"
    async with services.sm() as session:
        assert (await session.get(LlmModel, model_id)).supports_json_schema is True


async def test_check_makes_a_tiny_call(client, services, root, fake_llm):
    fake_llm.push(chat_reply("OK", tokens_in=12, tokens_out=1, cost=0.000002))
    resp = await client.post(
        "/v1/admin/models/check", json={"name": "openai/gpt-6-luna"}, headers=root
    )
    out = resp.json()
    assert out["ok"] is True
    assert out["answer"] == "OK"
    assert fake_llm.models_called() == ["openai/gpt-6-luna"]


async def test_check_reports_a_failure(client, services, root, fake_llm):
    fake_llm.push(LLMTransportError("boom", status=502))
    resp = await client.post("/v1/admin/models/check", json={"name": "openai/nope"}, headers=root)
    out = resp.json()
    assert out["ok"] is False
    assert out["error"]["code"] == "models.check_failed"


async def test_check_embedding(client, services, root, fake_llm):
    resp = await client.post(
        "/v1/admin/models/check",
        json={"name": "openai/embed-x", "kind": "embedding"},
        headers=root,
    )
    out = resp.json()
    assert out["ok"] is True
    assert out["dimensions"] == fake_llm.embed_dim


# --- roles --------------------------------------------------------------------------------


async def _say(client, bundle, text: str) -> dict:
    resp = await client.post(
        "/v1/messages",
        json={"contact": {"external_id": "c1"}, "text": text},
        headers=bundle.auth,
    )
    return resp.json()


async def test_switch_works_from_the_next_turn(client, services, root, fake_llm):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    await make_model(services.sm, "openai/b", roles=())
    fake_llm.default = chat_reply("hi")

    await _say(client, bundle, "one")
    resp = await client.put("/v1/admin/roles/reply", json={"model": "openai/b"}, headers=root)
    assert resp.status_code == 200, resp.text
    await _say(client, bundle, "two")

    assert fake_llm.models_called() == ["openai/a", "openai/b"]


async def test_tenant_override_and_reset(client, services, root, fake_llm):
    bundle = await make_tenant(services.sm)
    tid = bundle.tenant_id
    adm = await admin_headers(services, "adm@example.com", tenant_id=tid)
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    await make_model(services.sm, "openai/b", roles=())
    fake_llm.default = chat_reply("hi")

    resp = await client.put(
        "/v1/admin/roles/reply", json={"tenant_id": str(tid), "model": "openai/b"}, headers=adm
    )
    assert resp.status_code == 200, resp.text
    roles = (await client.get(f"/v1/admin/roles?tenant_id={tid}", headers=adm)).json()["items"]
    reply = next(r for r in roles if r["role"] == "reply")
    assert reply["effective"] == "own"
    assert reply["own"]["model"] == "openai/b"
    assert reply["shared"]["model"] == "openai/a"
    await _say(client, bundle, "one")

    assert (
        await client.delete(f"/v1/admin/roles/reply?tenant_id={tid}", headers=adm)
    ).status_code == 200
    await _say(client, bundle, "two")
    assert fake_llm.models_called() == ["openai/b", "openai/a"]


async def test_role_without_a_model_is_flagged(client, services, root):
    items = (await client.get("/v1/admin/roles", headers=root)).json()["items"]
    problems = {r["role"]: r["problem"] for r in items}
    assert problems["reply"] == "llm.role_not_assigned"
    assert set(problems) == {r.value for r in LlmRole}


async def test_turn_without_a_model_fails_clearly(client, services):
    bundle = await make_tenant(services.sm)
    resp = await client.post(
        "/v1/messages", json={"contact": {"external_id": "c1"}, "text": "hi"}, headers=bundle.auth
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "llm.role_not_assigned"
    assert resp.json()["error"]["params"]["role"] == "reply"


async def test_role_needing_json_rejects_a_plain_model(client, services, root):
    await make_model(services.sm, "openai/plain", roles=(), supports_json_schema=False)
    resp = await client.put("/v1/admin/roles/rewrite", json={"model": "openai/plain"}, headers=root)
    assert resp.json()["error"]["code"] == "models.json_schema_required"
    async with services.sm() as session:
        assert list((await session.execute(select(ModelRole))).scalars()) == []


async def test_reasoning_effort_must_be_supported(client, services, root):
    await make_model(
        services.sm,
        "openai/r",
        roles=(),
        supports_reasoning=True,
        reasoning_efforts=["low", "high"],
    )
    bad = await client.put(
        "/v1/admin/roles/reply",
        json={"model": "openai/r", "reasoning_effort": "extreme"},
        headers=root,
    )
    assert bad.json()["error"]["code"] == "models.reasoning_effort_invalid"
    ok = await client.put(
        "/v1/admin/roles/reply", json={"model": "openai/r", "reasoning_effort": "low"}, headers=root
    )
    assert ok.json()["assignment"]["reasoning_effort"] == "low"


async def test_tenant_admin_cannot_change_the_shared_role(client, services):
    bundle = await make_tenant(services.sm)
    adm = await admin_headers(services, "adm@example.com", tenant_id=bundle.tenant_id)
    await make_model(services.sm, "openai/a", roles=())
    resp = await client.put("/v1/admin/roles/reply", json={"model": "openai/a"}, headers=adm)
    assert resp.status_code == 403


# --- costs --------------------------------------------------------------------------------


async def test_costs_by_model_and_role(client, services, fake_llm, clock):
    # call rows get the DB time; "today" is counted from the clock. The session is opened
    # after the clock jump, or it would be expired.
    clock.set(datetime.now(UTC))
    root = await admin_headers(services, "root@example.com", superadmin=True)
    bundle = await make_tenant(services.sm)
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    fake_llm.default = chat_reply("hi", cost=0.002)
    await _say(client, bundle, "one")
    await _say(client, bundle, "two")

    out = (await client.get(f"/v1/admin/costs?tenant_id={bundle.tenant_id}", headers=root)).json()
    row = next(r for r in out["by_model_role"] if r["role"] == "reply")
    assert row["model"] == "openai/a"
    assert row["calls"] == 2
    assert Decimal(row["cost"]) == Decimal("0.004")
    assert Decimal(out["today"]) == Decimal("0.004")

    other = await make_tenant(services.sm, slug="other", title="Other")
    empty = (await client.get(f"/v1/admin/costs?tenant_id={other.tenant_id}", headers=root)).json()
    assert empty["by_model_role"] == []


async def test_global_costs_need_superadmin(client, services):
    bundle = await make_tenant(services.sm)
    adm = await admin_headers(services, "adm@example.com", tenant_id=bundle.tenant_id)
    assert (await client.get("/v1/admin/costs", headers=adm)).status_code == 403
    own = await client.get(f"/v1/admin/costs?tenant_id={bundle.tenant_id}", headers=adm)
    assert own.status_code == 200
