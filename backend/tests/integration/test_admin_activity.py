"""Results, conversations with the turn trace, and the sandbox in the admin."""

import pytest
from sqlalchemy import select

from app.db.models import (
    Contact,
    Conversation,
    LlmRole,
    Message,
    RunStatus,
    ScenarioRun,
    TurnStatus,
)
from app.results.delivery import queue_delivery
from app.scenarios.store import load_definition
from tests.admin_helpers import admin_headers
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import (
    deliveries_of,
    install_scenario,
    interview_data,
    make_def,
    make_endpoint,
    states,
)

pytestmark = pytest.mark.db

LANGS = {"languages": ["uk", "en"]}


@pytest.fixture
async def tenant(services):
    return await make_tenant(services.sm, settings=LANGS)


@pytest.fixture
async def adm(services, tenant):
    return await admin_headers(services, "adm@example.com", tenant_id=tenant.tenant_id)


def _t(tenant, tail: str) -> str:
    return f"/v1/admin/tenants/{tenant.tenant_id}{tail}"


async def _make_run(services, tenant, *, sandbox: bool = False, name: str = "Olha"):
    scenario_id = await install_scenario(
        services.sm, tenant.tenant_id, tenant.channel_id, interview_data()
    )
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        contact = Contact(
            tenant_id=tenant.tenant_id,
            channel_id=tenant.channel_id,
            external_id=f"c-{name}",
            name=name,
        )
        session.add(contact)
        await session.flush()
        conv = Conversation(
            tenant_id=tenant.tenant_id,
            channel_id=tenant.channel_id,
            contact_id=contact.id,
            sandbox=sandbox,
        )
        session.add(conv)
        await session.flush()
        run = ScenarioRun(
            tenant_id=tenant.tenant_id,
            channel_id=tenant.channel_id,
            conversation_id=conv.id,
            scenario_id=scenario_id,
            scenario_key="interview",
            scenario_version=1,
            status=RunStatus.COMPLETED,
            fields=states(make_def(), niche="торти", offer="торти на замовлення"),
            events=[],
            delivery_seq=0,
            client_language="ru",
            reply_language="uk",
            started_at=now,
            finished_at=now,
            last_activity_at=now,
        )
        session.add(run)
        await session.flush()
        return run.id


# --- results ------------------------------------------------------------------------------


async def test_results_list_and_detail(client, services, tenant, adm):
    run_id = await _make_run(services, tenant)
    out = (await client.get(_t(tenant, "/results"), headers=adm)).json()
    assert out["total"] == 1
    item = out["items"][0]
    assert item["id"] == str(run_id)
    assert item["contact"]["name"] == "Olha"
    assert item["fields"]["niche"] == "торти"
    assert "positioning" in item["missing"]

    detail = (await client.get(_t(tenant, f"/results/{run_id}"), headers=adm)).json()
    assert detail["result"]["client_language"] == "ru"
    assert detail["result"]["field_states"]["niche"]["state"] == "filled"
    assert detail["deliveries"] == []


async def test_results_filters(client, services, tenant, adm):
    await _make_run(services, tenant, name="Olha")
    out = (await client.get(_t(tenant, "/results?status=completed,focus"), headers=adm)).json()
    assert out["total"] == 1
    out = (await client.get(_t(tenant, "/results?status=cancelled"), headers=adm)).json()
    assert out["total"] == 0
    out = (await client.get(_t(tenant, "/results?q=olh"), headers=adm)).json()
    assert out["total"] == 1
    out = (await client.get(_t(tenant, "/results?scenario=other"), headers=adm)).json()
    assert out["total"] == 0


async def test_sandbox_results_are_hidden_by_default(client, services, tenant, adm):
    await _make_run(services, tenant, sandbox=True)
    assert (await client.get(_t(tenant, "/results"), headers=adm)).json()["total"] == 0
    out = (await client.get(_t(tenant, "/results?sandbox=true"), headers=adm)).json()
    assert out["total"] == 1


async def test_resend_delivery(client, services, tenant, adm):
    run_id = await _make_run(services, tenant)
    await make_endpoint(services, tenant.tenant_id, url="https://crm.example.com/hook")
    async with services.sm() as session, session.begin():
        run = await session.get(ScenarioRun, run_id, with_for_update=True)
        defn = await load_definition(session, run.scenario_id)
        first = await queue_delivery(
            session,
            run,
            defn,
            endpoint_name="results_api",
            event="completed",
            status="completed",
            now=services.clock.now(),
        )
        first_id = first.id

    resp = await client.post(_t(tenant, f"/deliveries/{first_id}/resend"), headers=adm)
    assert resp.status_code == 200, resp.text
    assert resp.json()["delivery"]["seq"] == 2
    assert resp.json()["delivery"]["endpoint"] == "results_api"
    rows = await deliveries_of(services.sm, run_id)
    assert [r.seq for r in rows] == [1, 2]

    detail = (await client.get(_t(tenant, f"/results/{run_id}"), headers=adm)).json()
    assert len(detail["deliveries"]) == 2


async def test_operator_cannot_resend(client, services, tenant):
    op = await admin_headers(
        services, "op@example.com", tenant_id=tenant.tenant_id, role="operator"
    )
    resp = await client.post(
        _t(tenant, "/deliveries/00000000-0000-0000-0000-000000000000/resend"), headers=op
    )
    assert resp.status_code == 403


async def test_resend_of_another_tenants_delivery(client, services, tenant, adm):
    other = await make_tenant(services.sm, slug="other", title="Other", settings=LANGS)
    run_id = await _make_run(services, other)
    await make_endpoint(services, other.tenant_id, url="https://crm.example.com/hook")
    async with services.sm() as session, session.begin():
        run = await session.get(ScenarioRun, run_id, with_for_update=True)
        defn = await load_definition(session, run.scenario_id)
        d = await queue_delivery(
            session,
            run,
            defn,
            endpoint_name="results_api",
            event="completed",
            status="completed",
            now=services.clock.now(),
        )
        d_id = d.id
    resp = await client.post(_t(tenant, f"/deliveries/{d_id}/resend"), headers=adm)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "delivery.not_found"


# --- conversations and the trace ----------------------------------------------------------


async def test_conversation_with_turn_trace(client, services, tenant, adm, fake_llm):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    fake_llm.default = chat_reply("Вітаю!")
    resp = await client.post(
        "/v1/messages",
        json={"contact": {"external_id": "c1", "name": "Olha"}, "text": "привіт"},
        headers=tenant.auth,
    )
    assert resp.status_code == 200, resp.text
    conv_id = resp.json()["conversation_id"]
    turn_id = resp.json()["turn_id"]

    listed = (await client.get(_t(tenant, "/conversations"), headers=adm)).json()
    assert listed["total"] == 1
    assert listed["items"][0]["last_message"]["text"] == "Вітаю!"

    detail = (await client.get(_t(tenant, f"/conversations/{conv_id}"), headers=adm)).json()
    assert [m["text"] for m in detail["messages"]] == ["привіт", "Вітаю!"]
    assert detail["turns"][0]["id"] == turn_id

    trace = (await client.get(_t(tenant, f"/turns/{turn_id}"), headers=adm)).json()
    assert trace["turn"]["input_text"] == "привіт"
    assert trace["turn"]["trace"]
    assert [c["model"] for c in trace["llm_calls"]] == ["openai/a"]
    assert {m["role"] for m in trace["messages"]} == {"client", "bot"}


async def test_conversation_search(client, services, tenant, adm):
    await _make_run(services, tenant, name="Olha")
    await _make_run(services, tenant, name="Petro")
    out = (await client.get(_t(tenant, "/conversations?q=petr"), headers=adm)).json()
    assert [i["contact"]["name"] for i in out["items"]] == ["Petro"]
    assert out["items"][0]["open_runs"] == 0


async def test_turn_of_another_tenant(client, services, tenant, adm, fake_llm):
    other = await make_tenant(services.sm, slug="other", title="Other")
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    fake_llm.default = chat_reply("hi")
    resp = await client.post(
        "/v1/messages",
        json={"contact": {"external_id": "c1"}, "text": "hi"},
        headers=other.auth,
    )
    turn_id = resp.json()["turn_id"]
    out = await client.get(_t(tenant, f"/turns/{turn_id}"), headers=adm)
    assert out.status_code == 404


# --- sandbox ------------------------------------------------------------------------------


async def test_sandbox_turn_answers_and_stays_hidden(client, services, tenant, adm, fake_llm):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    fake_llm.default = chat_reply("Вітаю!")
    resp = await client.post(_t(tenant, "/sandbox/turn"), json={"text": "привіт"}, headers=adm)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["reply"] == "Вітаю!"
    assert out["error"] is None
    assert out["turn"]["turn"]["sandbox"] is True
    assert [m["text"] for m in out["state"]["messages"]] == ["привіт", "Вітаю!"]

    async with services.sm() as session:
        conv = await session.get(Conversation, out["conversation_id"])
    assert conv.sandbox is True
    assert (await client.get(_t(tenant, "/conversations"), headers=adm)).json()["total"] == 0
    hidden = (await client.get(_t(tenant, "/conversations?sandbox=true"), headers=adm)).json()
    assert hidden["total"] == 1


async def test_sandbox_sessions_and_reset(client, services, tenant, adm, fake_llm):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    fake_llm.default = chat_reply("ok")
    for session_name in ("a", "b"):
        await client.post(
            _t(tenant, "/sandbox/turn"), json={"text": "hi", "session": session_name}, headers=adm
        )
    sessions = (await client.get(_t(tenant, "/sandbox/sessions"), headers=adm)).json()["items"]
    assert sorted(s["session"] for s in sessions) == ["a", "b"]

    state = (await client.get(_t(tenant, "/sandbox?session=a"), headers=adm)).json()
    assert state["state"] is not None
    assert (await client.delete(_t(tenant, "/sandbox?session=a"), headers=adm)).status_code == 200
    state = (await client.get(_t(tenant, "/sandbox?session=a"), headers=adm)).json()
    assert state["state"] is None
    left = (await client.get(_t(tenant, "/sandbox/sessions"), headers=adm)).json()["items"]
    assert [s["session"] for s in left] == ["b"]


async def test_sandbox_of_two_admins_do_not_mix(client, services, tenant, adm, fake_llm):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    fake_llm.default = chat_reply("ok")
    other = await admin_headers(services, "adm2@example.com", tenant_id=tenant.tenant_id)
    one = (await client.post(_t(tenant, "/sandbox/turn"), json={"text": "a"}, headers=adm)).json()
    two = (await client.post(_t(tenant, "/sandbox/turn"), json={"text": "b"}, headers=other)).json()
    assert one["conversation_id"] != two["conversation_id"]


async def test_sandbox_model_override_leaves_the_role_alone(
    client, services, tenant, adm, fake_llm
):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    await make_model(services.sm, "openai/b", roles=())
    fake_llm.default = chat_reply("ok")
    resp = await client.post(
        _t(tenant, "/sandbox/turn"), json={"text": "hi", "model": "openai/b"}, headers=adm
    )
    assert resp.json()["error"] is None
    await client.post(_t(tenant, "/sandbox/turn"), json={"text": "again"}, headers=adm)
    assert fake_llm.models_called() == ["openai/b", "openai/a"]


async def test_sandbox_refuses_a_model_without_json(client, services, tenant, adm, fake_llm):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    await make_model(services.sm, "openai/plain", roles=(), supports_json_schema=False)
    resp = await client.post(
        _t(tenant, "/sandbox/turn"), json={"text": "hi", "model": "openai/plain"}, headers=adm
    )
    assert resp.json()["error"]["code"] == "sandbox.model_unsuitable"
    assert fake_llm.calls == 0


async def test_sandbox_shows_a_failed_turn(client, services, tenant, adm, fake_llm):
    # no model on the reply role: the turn fails, the admin sees why
    resp = await client.post(_t(tenant, "/sandbox/turn"), json={"text": "hi"}, headers=adm)
    assert resp.status_code == 200
    out = resp.json()
    assert out["reply"] is None
    assert out["error"]["code"] == "llm.role_not_assigned"
    assert out["turn"]["turn"]["status"] == TurnStatus.FAILED
    # the failed message is not answered again with the next one
    async with services.sm() as session:
        msg = (await session.execute(select(Message).where(Message.text == "hi"))).scalar_one()
    assert msg.turn_id is not None


async def test_sandbox_sends_a_file_by_link(client, services, tenant, adm, fake_llm):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    fake_llm.default = chat_reply("Бачу фото.")
    files = [{"type": "image", "url": "https://cdn.example/receipt.jpg"}]
    resp = await client.post(_t(tenant, "/sandbox/turn"), json={"attachments": files}, headers=adm)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    first = out["state"]["messages"][0]
    assert first["text"] == "[image]"
    assert first["attachments"] == files


async def test_sandbox_needs_words_or_a_file(client, services, tenant, adm):
    resp = await client.post(_t(tenant, "/sandbox/turn"), json={"text": "  "}, headers=adm)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "sandbox.text_required"
