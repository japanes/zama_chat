"""Stage 7: the decision rules (API, audit, how a turn reads them), unsure answers that go
to an operator, the stats and disagreements of the admin page, decision_mode of the
sandbox, per-role overrides of `make eval`."""

import importlib.util
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

from app.core.ingest import save_client_message
from app.core.pipeline import full_pipeline
from app.db.models import (
    AuditLog,
    BelowAction,
    Decision,
    DecisionProvider,
    DecisionRule,
    LlmRole,
    OperatorTask,
    PolicyMode,
    TaskKind,
    Turn,
)
from app.decisions.engine import DecideContext, DecisionSet, run_decisions
from app.decisions.fake import FakeDecider
from app.decisions.policy import Rule, load_policy
from app.llm.service import Overrides
from app.prompts.seed import seed_prompts
from tests.admin_helpers import admin_headers
from tests.conftest import chat_reply
from tests.decision_helpers import yes_no
from tests.factories import make_model, make_tenant
from tests.integration.test_turn import Scripted
from tests.unit.test_jev_decider import FakeFallback, Gateway, make_decider, ok

pytestmark = pytest.mark.db

LANGS = {"languages": ["uk", "en"], "reply_language": {"uk": "uk", "ru": "uk", "*": "en"}}


@pytest.fixture
async def tenant(services):
    return await make_tenant(services.sm, settings=LANGS)


@pytest.fixture
async def adm(services, tenant):
    return await admin_headers(services, "adm@example.com", tenant_id=tenant.tenant_id)


@pytest.fixture
async def sup(services):
    return await admin_headers(services, "root@example.com", superadmin=True)


def _t(tenant, tail: str) -> str:
    return f"/v1/admin/tenants/{tenant.tenant_id}{tail}"


# --- rules API ----------------------------------------------------------------------------


async def test_tenant_rules_replace_and_read(client, services, tenant, adm):
    body = {
        "rules": [
            {"pattern": "start:*", "mode": "shadow", "threshold": 0.85, "note": " check "},
            {"pattern": "wants_human", "mode": "jev", "threshold": 0.9, "below": "operator"},
        ]
    }
    resp = await client.put(_t(tenant, "/decisions/policy"), json=body, headers=adm)
    assert resp.status_code == 200, resp.text
    own = resp.json()["own"]
    assert [r["pattern"] for r in own] == ["start:*", "wants_human"]
    assert own[0]["note"] == "check"
    assert own[1]["below"] == "operator"

    # the second PUT replaces, not adds
    body = {"rules": [{"pattern": "field:*", "threshold": 0.6}]}
    await client.put(_t(tenant, "/decisions/policy"), json=body, headers=adm)
    out = (await client.get(_t(tenant, "/decisions/policy"), headers=adm)).json()
    assert [r["pattern"] for r in out["own"]] == ["field:*"]
    assert out["own"][0]["mode"] == "jev" and out["own"][0]["below"] == "llm"
    assert out["shared"] == []
    assert out["can_edit_shared"] is False
    assert out["default_rule"]["threshold"] == Rule().threshold

    async with services.sm() as session:
        logs = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "decision_policy.update")
                )
            ).scalars()
        )
    assert len(logs) == 2
    assert logs[-1].tenant_id == tenant.tenant_id
    assert logs[-1].data["rules"][0]["pattern"] == "field:*"


@pytest.mark.parametrize(
    ("rules", "code"),
    [
        ([{"pattern": "bad pattern"}], "decision_policy.pattern_invalid"),
        ([{"pattern": "a"}, {"pattern": " a "}], "decision_policy.pattern_duplicate"),
    ],
)
async def test_bad_rules_are_refused(client, tenant, adm, rules, code):
    resp = await client.put(_t(tenant, "/decisions/policy"), json={"rules": rules}, headers=adm)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == code


async def test_threshold_out_of_range_is_refused(client, tenant, adm):
    body = {"rules": [{"pattern": "a", "threshold": 1.5}]}
    resp = await client.put(_t(tenant, "/decisions/policy"), json=body, headers=adm)
    assert resp.status_code == 422


async def test_shared_rules_need_a_superadmin(client, services, tenant, adm, sup):
    body = {"rules": [{"pattern": "*", "threshold": 0.75}]}
    assert (
        await client.put("/v1/admin/decisions/policy", json=body, headers=adm)
    ).status_code == 403
    resp = await client.put("/v1/admin/decisions/policy", json=body, headers=sup)
    assert resp.status_code == 200, resp.text

    # a tenant admin reads them, under its own rules
    out = (await client.get(_t(tenant, "/decisions/policy"), headers=adm)).json()
    assert [r["pattern"] for r in out["shared"]] == ["*"]
    shared = (await client.get("/v1/admin/decisions/policy", headers=adm)).json()
    assert shared["shared"][0]["threshold"] == 0.75


async def test_operator_cannot_see_the_rules(client, services, tenant):
    op = await admin_headers(
        services, "op@example.com", tenant_id=tenant.tenant_id, role="operator"
    )
    assert (await client.get(_t(tenant, "/decisions/policy"), headers=op)).status_code == 403
    assert (await client.get(_t(tenant, "/decisions/stats"), headers=op)).status_code == 403


async def test_another_tenant_rules_do_not_leak(client, services, tenant, adm):
    other = await make_tenant(services.sm, slug="other", settings=LANGS)
    async with services.sm() as session, session.begin():
        session.add(DecisionRule(tenant_id=other.tenant_id, pattern="x", threshold=0.5))
    out = (await client.get(_t(tenant, "/decisions/policy"), headers=adm)).json()
    assert out["own"] == [] and out["shared"] == []


# --- how a turn reads the rules ------------------------------------------------------------


class Recording:
    """A decider that only remembers the context it got."""

    def __init__(self) -> None:
        self.ctx: DecideContext | None = None

    async def decide(self, state, questions, ctx) -> DecisionSet:
        self.ctx = ctx
        return DecisionSet()


async def test_run_decisions_reads_the_rules_and_the_forced_mode(services, tenant):
    async with services.sm() as session, session.begin():
        session.add(DecisionRule(tenant_id=None, pattern="a", threshold=0.8))
        session.add(
            DecisionRule(
                tenant_id=tenant.tenant_id,
                pattern="a",
                mode=PolicyMode.JEV,
                threshold=0.9,
                below=BelowAction.KEEP,
            )
        )
    rec = Recording()
    services.extra["decider"] = rec
    ctx = DecideContext(tenant.tenant_id, None, Overrides(decision_mode=PolicyMode.SHADOW))
    await run_decisions(services, "s", [yes_no("a")], ctx)
    assert rec.ctx is not None and rec.ctx.policy is not None
    # the tenant's rule replaced the shared one; the sandbox mode replaced its mode
    assert rec.ctx.policy.rule("a") == Rule("a", PolicyMode.SHADOW, 0.9, BelowAction.KEEP)


async def test_run_decisions_collects_operator_keys_and_logs_unsure(services, tenant):
    services.extra["decider"] = FakeDecider(
        {"*": True}, unsure={"a": BelowAction.OPERATOR, "b": BelowAction.ASK_CLIENT}
    )
    sink: list[str] = []
    ctx = DecideContext(tenant.tenant_id, None, policy=None, unsure=sink)
    result = await run_decisions(services, "s", [yes_no("a"), yes_no("b"), yes_no("c")], ctx)
    assert sink == ["a"]
    assert result.value("b") is None and result.value("c") is True
    async with services.sm() as session:
        rows = {
            r.key: r
            for r in (
                await session.execute(
                    select(Decision).where(Decision.tenant_id == tenant.tenant_id)
                )
            ).scalars()
        }
    assert (rows["a"].reason, rows["a"].used) == ("unsure", False)
    assert (rows["c"].reason, rows["c"].used) == ("primary", True)


async def test_unsure_route_answer_sends_the_turn_to_an_operator(
    client, services, tenant, fake_llm
):
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY, LlmRole.REWRITE))
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = FakeDecider(
        {"service_type": "none", "wants_human": False},
        unsure={"wants_human": BelowAction.OPERATOR},
    )
    rewrite = {"question": ["Можна поговорити з кимось?"], "language": "uk"}
    variants = {"variants": ["Так, звісно.", "Зараз покличу."]}
    fake_llm.push(chat_reply(json.dumps(rewrite)), chat_reply(json.dumps(variants)))
    resp = await client.post(
        "/v1/messages",
        json={"contact": {"external_id": "c1"}, "text": "можна з кимось поговорити?"},
        headers=tenant.auth,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["waiting_operator"] is True

    async with services.sm() as session:
        task = (await session.execute(select(OperatorTask))).scalar_one()
        turn = await session.get(Turn, uuid.UUID(resp.json()["turn_id"]))
    assert task.kind == TaskKind.CHOOSE_REPLY
    assert task.payload["variants"] == ["Так, звісно.", "Зараз покличу."]
    assert turn is not None
    respond = next(s for s in turn.trace["steps"] if s["step"] == "respond")
    assert respond["unsure"] == ["wants_human"]
    # unsure is not «yes»: no handover
    assert turn.route["wants_human"] is False


async def test_unsure_filter_means_ask_again(services, tenant):
    """check_filters: an unsure filter is a failed one (the client is asked again)."""
    from app.scenarios.checks import CheckItem, check_filters
    from app.scenarios.fields import load_states
    from tests.scenario_helpers import interview_data, make_def
    from tests.turn_ctx import make_ctx

    defn = make_def(interview_data())
    field = next(f for f in defn.fields if f.filters)
    flt = field.filters[0].key
    services.extra["decider"] = FakeDecider(
        {"*": True}, unsure={f"filter:*.{field.key}.{flt}": BelowAction.ASK_CLIENT}
    )
    ctx = make_ctx(tenant_id=tenant.tenant_id, turn_id=None)  # no turn row: decisions log it
    item = CheckItem(defn, field, "відповідь", load_states(defn, None))
    failed = await check_filters(services, ctx, [item], "uk")
    assert flt in failed[(defn.key, field.key)]


# --- the rule that comes with the system --------------------------------------------------

MIGRATIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
OPTIONS_RULE = "4c97f46f4c0d"  # the shared rule of the catalog options (opt:*)


async def _migrate(services, revision: str, step: str) -> None:
    """Runs upgrade() / downgrade() of one migration on the test database."""
    (path,) = MIGRATIONS.glob(f"*_{revision}_*.py")
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(sync_session) -> None:
        with Operations.context(MigrationContext.configure(sync_session.connection())):
            getattr(module, step)()

    async with services.sm() as session, session.begin():
        await session.run_sync(run)


async def test_the_catalog_options_check_keeps_the_unsure_jev_answer(services, tenant):
    await _migrate(services, OPTIONS_RULE, "upgrade")
    async with services.sm() as session:
        policy = await load_policy(session, tenant.tenant_id)
    assert policy.rule("opt:3") == Rule("opt:*", PolicyMode.JEV, 0.7, BelowAction.KEEP)

    # Jev is unsure about both: the option keeps its answer, the search candidate goes
    # to the LLM as before
    gateway = Gateway(ok({"opt:3": {"probability": 0.35}, "cand:3": {"probability": 0.35}}))
    fallback = FakeFallback({"cand:3": True})
    ctx = DecideContext(tenant.tenant_id, None, policy=policy)
    result = await make_decider(gateway, fallback=fallback).decide(
        "s", [yes_no("opt:3"), yes_no("cand:3")], ctx
    )
    assert fallback.calls == [["cand:3"]]
    assert result.value("opt:3") is False
    assert result.answers["opt:3"].provider == DecisionProvider.JEV
    assert result.value("cand:3") is True


async def test_the_options_rule_does_not_touch_the_admins_rule(services, tenant):
    async with services.sm() as session, session.begin():
        session.add(DecisionRule(tenant_id=None, pattern="opt:*", threshold=0.6, note="mine"))
    await _migrate(services, OPTIONS_RULE, "upgrade")
    await _migrate(services, OPTIONS_RULE, "downgrade")
    async with services.sm() as session:
        rows = list((await session.execute(select(DecisionRule))).scalars())
    assert [(r.pattern, r.threshold, r.below, r.note) for r in rows] == [
        ("opt:*", 0.6, BelowAction.LLM, "mine")
    ]


async def test_the_options_rule_is_added_once_and_removed_by_the_downgrade(services):
    await _migrate(services, OPTIONS_RULE, "upgrade")
    await _migrate(services, OPTIONS_RULE, "upgrade")
    async with services.sm() as session:
        rows = list((await session.execute(select(DecisionRule))).scalars())
    assert [(r.tenant_id, r.pattern, r.below) for r in rows] == [(None, "opt:*", BelowAction.KEEP)]
    await _migrate(services, OPTIONS_RULE, "downgrade")
    async with services.sm() as session:
        assert list((await session.execute(select(DecisionRule))).scalars()) == []


# --- stats and disagreements ----------------------------------------------------------------


async def _turn(services, tenant, text: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with services.sm() as session, session.begin():
        ingested = await save_client_message(
            session,
            tenant_id=tenant.tenant_id,
            channel_id=tenant.channel_id,
            contact_external_id=f"c-{uuid.uuid4().hex[:6]}",
            text=text,
            now=services.clock.now(),
        )
        turn = Turn(
            tenant_id=tenant.tenant_id,
            conversation_id=ingested.conversation_id,
            input_text=text,
            rewritten=text.capitalize(),
        )
        session.add(turn)
        await session.flush()
        return turn.id, ingested.conversation_id


def _row(tenant, turn_id, key, provider, value, confidence, **kw) -> Decision:
    return Decision(
        tenant_id=tenant.tenant_id,
        turn_id=turn_id,
        batch_id=uuid.uuid4(),
        key=key,
        type="boolean",
        question={"instructions": "Is it so?", "criteria": {"true": "yes", "false": "no"}},
        answer=None if value is None else {"value": value},
        confidence=confidence,
        provider=provider,
        model="typesafe-ai/jev" if provider == DecisionProvider.JEV else "openai/a",
        **kw,
    )


async def _seed_stats(services, tenant) -> dict[str, uuid.UUID]:
    t1, _ = await _turn(services, tenant, "хочу записатися")
    t2, _ = await _turn(services, tenant, "може в п'ятницю")
    t3, _ = await _turn(services, tenant, "дякую")
    jev, llm = DecisionProvider.JEV, DecisionProvider.LLM
    rows = [
        # t1: sure Jev + the shadow LLM agrees
        _row(tenant, t1, "start:b.wants", jev, True, 0.97, cost=Decimal("0.0001")),
        _row(tenant, t1, "start:b.wants", llm, True, 0.9, reason="shadow", used=False),
        # t2: unsure Jev, the LLM disagrees and is used
        _row(tenant, t2, "start:b.wants", jev, True, 0.62, used=False),
        _row(tenant, t2, "start:b.wants", llm, False, 0.8, reason="below_threshold"),
        # t3: Jev failed
        _row(tenant, t3, "start:b.wants", jev, None, None, used=False, error="502: x"),
        _row(tenant, t3, "start:b.wants", llm, False, 0.8, reason="jev_error"),
        # numbered keys fall into one group
        _row(tenant, t1, "cand:12", jev, True, 0.99),
        _row(tenant, t1, "cand:7", jev, False, 0.55, reason="unsure", used=False),
    ]
    old = _row(tenant, t1, "old", jev, True, 0.9)
    old.created_at = services.clock.now() - timedelta(days=30)
    async with services.sm() as session, session.begin():
        session.add_all([*rows, old])
        session.add(DecisionRule(tenant_id=None, pattern="start:*", threshold=0.8))
    return {"t1": t1, "t2": t2, "t3": t3}


async def test_stats_by_group(client, services, tenant, adm):
    await _seed_stats(services, tenant)
    resp = await client.get(_t(tenant, "/decisions/stats?days=7"), headers=adm)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    items = {i["group"]: i for i in out["items"]}
    assert set(items) == {"start:b.wants", "cand:*"}  # "old" is outside the period

    wants = items["start:b.wants"]
    assert wants["asked"] == 3
    assert (wants["jev"], wants["jev_used"], wants["jev_failed"]) == (3, 1, 1)
    assert (wants["below_threshold"], wants["jev_error"], wants["shadow"]) == (1, 1, 1)
    assert wants["llm_only"] == 0
    assert (wants["pairs"], wants["agree"]) == (2, 1)
    assert wants["rule"]["pattern"] == "start:*" and wants["rule"]["threshold"] == 0.8
    assert wants["rule_source"] == "shared"
    assert len(wants["buckets"]) == 20
    by_from = {b["from"]: b for b in wants["buckets"]}
    assert by_from[0.95] == {"from": 0.95, "jev": 1, "pairs": 1, "agree": 1}
    assert by_from[0.6] == {"from": 0.6, "jev": 1, "pairs": 1, "agree": 0}

    cand = items["cand:*"]
    assert (cand["asked"], cand["unsure"]) == (2, 1)
    assert cand["rule"]["pattern"] is None and cand["rule_source"] is None
    assert out["totals"]["asked"] == 5
    assert out["bucket_size"] == 0.05


async def test_stats_of_another_tenant_are_empty(client, services, tenant, adm):
    await _seed_stats(services, tenant)
    other = await make_tenant(services.sm, slug="other", settings=LANGS)
    adm2 = await admin_headers(services, "adm2@example.com", tenant_id=other.tenant_id)
    out = (await client.get(_t(other, "/decisions/stats"), headers=adm2)).json()
    assert out["items"] == []


async def test_disagreements(client, services, tenant, adm):
    turns = await _seed_stats(services, tenant)
    url = _t(tenant, "/decisions/disagreements?key=start:b.wants")
    out = (await client.get(url, headers=adm)).json()
    (item,) = out["items"]
    assert item["turn_id"] == str(turns["t2"])
    assert item["rewritten"] == "Може в п'ятницю"
    assert item["instructions"] == "Is it so?"
    assert item["jev"]["value"] is True and item["llm"]["value"] is False
    assert item["llm"]["reason"] == "below_threshold"

    agreed = (await client.get(url + "&agree=true", headers=adm)).json()["items"]
    assert [i["turn_id"] for i in agreed] == [str(turns["t1"])]


# --- sandbox and `make eval` overrides ---------------------------------------------------------


async def test_sandbox_decision_mode_reaches_the_turn(client, services, tenant, adm, fake_llm):
    from app.core.context import TurnPlan

    pipeline = Scripted(TurnPlan(reply_text="ok"))
    services.extra["pipeline"] = pipeline
    resp = await client.post(
        _t(tenant, "/sandbox/turn"),
        json={"text": "hi", "decision_mode": "shadow"},
        headers=adm,
    )
    assert resp.status_code == 200, resp.text
    ctx = pipeline.contexts[0]
    assert ctx.overrides is not None and ctx.overrides.decision_mode == "shadow"
    assert ctx.policy is not None and ctx.policy.rule("x").mode == PolicyMode.SHADOW

    bad = await client.post(
        _t(tenant, "/sandbox/turn"), json={"text": "hi", "decision_mode": "both"}, headers=adm
    )
    assert bad.status_code == 422


async def test_role_overrides_by_role(services, tenant):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY, LlmRole.REWRITE))
    await make_model(services.sm, "openai/b", roles=())
    overrides = Overrides(
        models={"reply": "openai/b"}, reasoning_effort="low", efforts={"reply": ""}
    )
    reply = await services.llm.role_config(LlmRole.REPLY, tenant.tenant_id, overrides)
    rewrite = await services.llm.role_config(LlmRole.REWRITE, tenant.tenant_id, overrides)
    assert (reply.model.name, reply.reasoning_effort) == ("openai/b", None)
    assert (rewrite.model.name, rewrite.reasoning_effort) == ("openai/a", "low")
