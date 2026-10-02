"""Dialogs in YAML (tests/dialogs/*.yaml) played through POST /v1/messages on the full
pipeline: the fake LLM gives the scripted rewrite, extract and reply, FakeDecider the
route and the filters.

A file:

  name: …
  tenant: {settings: {languages: [uk, en], reply_language: {…}}}
  scenarios: [offer_interview]       # files from <project>/scenarios/, switched on in the
                                     # channel; results go to /dev/sink/results
  decide: {service_type: none, wants_human: false}   # answers for every turn; with
                                     # scenarios the defaults are: nothing starts, no field
                                     # is answered, every filter passes
  turns:
    - client: Привіт!
      wait_minutes: 20                                # optional: the clock moves first
      rewrite: {question: [Привіт], language: uk}     # what the rewrite model returns
      decide: {service_type: greeting_only}           # this turn only, over the defaults
      extract: {offer_interview__niche: …}            # what the extract model returns
                                                      # (only when it is called)
      reply: Вітаю!                                   # what the reply model returns
                                                      # (no rewrite / reply: a quiet turn,
                                                      # no model is asked — handed over)
      expect:                                         # all optional
        reply: Вітаю!
        prompt: service_greeting_only                 # prompt the reply was made with
        route: {service_type: greeting_only}          # subset of turns.route
        handover: false
        client_language: uk
        reply_language: uk
        rewritten: Привіт
        step: {ask_next: {question: …}}               # subset of the reply's scenario_step
        no_step: true                                 # the reply has no scenario_step
        quiet: true                                   # the bot said nothing
        scenario:                                     # the newest run of a scenario
          key: offer_interview
          status: focus
          asking: audience
          values: {niche: …}                          # field → value (null = no value)
          states: {niche: filled}
        sink:                                         # every result received so far,
          - {status: incomplete, fields: {…}}         # oldest first, each a subset

    - wait_minutes: 15                                # no client message: the clock moves,
      expect: {sink: […]}                             # due jobs run (silence, delivery)

After every step the due jobs run (Worker.run_due). Unknown keys are an error, so a typo
does not silently skip a check.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.core.pipeline import full_pipeline
from app.db.models import Conversation, LlmRole, ScenarioRun, Turn, TurnStatus
from app.decisions.fake import FakeDecider
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from app.main import create_app
from app.prompts.seed import seed_prompts
from app.scenarios.loader import read_file
from app.settings import PROJECT_ROOT
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import ROUTE_DEFAULTS, install_scenario, make_endpoint

pytestmark = pytest.mark.db

DIALOGS_DIR = Path(__file__).parent
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"
ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE, LlmRole.EXTRACT)
SINK = "results"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rewrite(Strict):
    question: list[str]
    language: str | None = None


class ExpectScenario(Strict):
    key: str
    status: str | None = None
    asking: str | None = None
    no_asking: bool = False
    values: dict[str, str | None] = Field(default_factory=dict)
    states: dict[str, str] = Field(default_factory=dict)


class Expect(Strict):
    reply: str | None = None
    prompt: str | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    handover: bool | None = None
    client_language: str | None = None
    reply_language: str | None = None
    rewritten: str | None = None
    step: dict[str, Any] | None = None
    no_step: bool = False
    quiet: bool = False
    scenario: ExpectScenario | None = None
    sink: list[dict[str, Any]] | None = None


class DialogTurn(Strict):
    client: str | None = None
    wait_minutes: float | None = None
    rewrite: Rewrite | None = None
    decide: dict[str, Any] = Field(default_factory=dict)
    extract: dict[str, Any] | None = None
    reply: str | None = None
    expect: Expect = Field(default_factory=Expect)

    @model_validator(mode="after")
    def _shape(self) -> "DialogTurn":
        if self.client is None:
            if self.wait_minutes is None:
                raise ValueError("a step needs `client` or `wait_minutes`")
            if self.rewrite or self.reply or self.extract or self.decide:
                raise ValueError("a wait step has no rewrite / decide / extract / reply")
        elif (self.rewrite is None) != (self.reply is None):
            raise ValueError("a client message needs both `rewrite` and `reply`, or neither")
        elif self.rewrite is None and (self.extract or self.decide):
            raise ValueError("a quiet turn has no decide / extract")
        return self


class TenantSpec(Strict):
    settings: dict[str, Any] = Field(default_factory=dict)


class Dialog(Strict):
    name: str
    tenant: TenantSpec = Field(default_factory=TenantSpec)
    scenarios: list[str] = Field(default_factory=list)
    decide: dict[str, Any] = Field(default_factory=dict)
    turns: list[DialogTurn]


def load_dialog(path: Path) -> Dialog:
    return Dialog.model_validate(yaml.safe_load(path.read_text("utf-8")))


DIALOG_FILES = sorted(DIALOGS_DIR.glob("*.yaml"))


def test_dialog_files_are_valid():
    assert DIALOG_FILES, "no dialogs in tests/dialogs/"
    for path in DIALOG_FILES:
        load_dialog(path)


@pytest.mark.parametrize("path", DIALOG_FILES, ids=lambda p: p.stem)
async def test_dialog(path, client, services, fake_llm):
    dialog = load_dialog(path)
    decider = FakeDecider()
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = decider
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES)
    bundle = await make_tenant(services.sm, settings=dialog.tenant.settings)
    defaults = dict(dialog.decide)
    if dialog.scenarios:
        defaults = {**ROUTE_DEFAULTS, **defaults}
        for name in dialog.scenarios:
            data = read_file(SCENARIOS_DIR / f"{name}.yaml")
            await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, data)
        # results reach the dev sink of the same app, no network
        await services.http.aclose()
        services.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)))
        await make_endpoint(
            services, bundle.tenant_id, url=f"http://test/dev/sink/{SINK}", secret="s3cret"
        )
    worker = Worker(services.sm, services.clock, HANDLERS, services=services)

    conversation_id: str | None = None
    for number, turn in enumerate(dialog.turns, 1):
        where = f"{path.name}, step {number} ({turn.client or f'wait {turn.wait_minutes} min'!r})"
        if turn.wait_minutes:
            services.clock.advance(minutes=turn.wait_minutes)
            await worker.run_due()  # timers that came due while the client was away
        body = None
        if turn.client is not None:
            decider.rules = {**defaults, **turn.decide}
            if turn.rewrite is not None and turn.reply is not None:
                rewrite = json.dumps(turn.rewrite.model_dump(), ensure_ascii=False)
                script = [chat_reply(rewrite)]
                if turn.extract is not None:
                    script.append(chat_reply(json.dumps(turn.extract, ensure_ascii=False)))
                script.append(chat_reply(turn.reply))
                fake_llm.push(*script)
            fake_llm.requests.clear()
            resp = await client.post(
                "/v1/messages",
                json={"contact": {"external_id": "c1", "name": "Olha"}, "text": turn.client},
                headers=bundle.auth,
            )
            assert resp.status_code == 200, f"{where}: {resp.text}"
            body = resp.json()
            conversation_id = body["conversation_id"]
            assert not fake_llm.script, f"{where}: unused LLM answers (`extract` given, not asked?)"
            await worker.run_due()
        if body is not None:
            await _check(services, body, turn.expect, where, fake_llm)
        await _check_scenarios(services, client, conversation_id, turn.expect, where)

    # every scripted LLM answer was used
    assert not fake_llm.script, f"{path.name}: unused LLM answers"


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _subset(v, actual[k]) for k, v in expected.items()
        )
    return expected == actual


def _scenario_step(fake_llm) -> dict[str, Any] | None:
    if not fake_llm.requests:
        return None  # a quiet turn
    content = fake_llm.requests[-1]["messages"][-1]["content"]
    try:
        data = json.loads(content)
    except ValueError:
        return None  # a service reply: the client's own text
    return data.get("scenario_step") if isinstance(data, dict) else None


async def _check_scenarios(
    services, client, conversation_id: str | None, expect: Expect, where: str
) -> None:
    if expect.scenario is not None:
        assert conversation_id is not None, f"{where}: no conversation yet"
        want = expect.scenario
        async with services.sm() as session:
            run = (
                await session.execute(
                    select(ScenarioRun)
                    .where(
                        ScenarioRun.conversation_id == uuid.UUID(conversation_id),
                        ScenarioRun.scenario_key == want.key,
                    )
                    .order_by(ScenarioRun.started_at.desc(), ScenarioRun.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        assert run is not None, f"{where}: no run of {want.key}"
        if want.status is not None:
            assert run.status == want.status, f"{where}: status {run.status}"
        if want.asking is not None:
            assert run.asking == want.asking, f"{where}: asking {run.asking}"
        if want.no_asking:
            assert run.asking is None, f"{where}: asking {run.asking}"
        for key, value in want.values.items():
            got = (run.fields.get(key) or {}).get("value")
            assert got == value, f"{where}: {want.key}.{key} = {got!r}"
        for key, state in want.states.items():
            got = (run.fields.get(key) or {}).get("state", "empty")
            assert got == state, f"{where}: {want.key}.{key} state {got!r}"
    if expect.sink is not None:
        resp = await client.get(f"/dev/sink/{SINK}")
        items = list(reversed(resp.json()["items"]))  # oldest first
        bodies = [i["body"] for i in items]
        assert len(bodies) == len(expect.sink), f"{where}: sink has {len(bodies)}: {bodies}"
        for i, (want_body, got) in enumerate(zip(expect.sink, bodies, strict=True)):
            assert _subset(want_body, got), f"{where}: sink[{i}] = {got}"


async def _check(services, body: dict, expect: Expect, where: str, fake_llm) -> None:
    async with services.sm() as session:
        turn = await session.get(Turn, uuid.UUID(body["turn_id"]))
        conv = await session.get(Conversation, uuid.UUID(body["conversation_id"]))
    assert turn is not None and conv is not None
    assert turn.status == TurnStatus.DONE, f"{where}: {turn.error}"

    if expect.reply is not None:
        assert body["reply"] == expect.reply, where
    if expect.quiet:
        assert body["reply"] is None, f"{where}: reply {body['reply']!r}"
        assert not fake_llm.requests, f"{where}: a model was asked"
    if expect.handover is not None:
        assert body["handover"] is expect.handover, where
        assert conv.handover is expect.handover, where
    if expect.prompt is not None:
        respond = next(s for s in turn.trace["steps"] if s["step"] == "respond")
        assert respond["prompt_key"] == expect.prompt, where
    for key, value in expect.route.items():
        assert (turn.route or {}).get(key) == value, f"{where}: route.{key}"
    if expect.client_language is not None:
        assert conv.client_language == expect.client_language, where
    if expect.reply_language is not None:
        assert conv.reply_language == expect.reply_language, where
    if expect.rewritten is not None:
        assert turn.rewritten == expect.rewritten, where
    step = _scenario_step(fake_llm)
    if expect.step is not None:
        assert step is not None, f"{where}: the reply has no scenario_step"
        assert _subset(expect.step, step), f"{where}: scenario_step = {step}"
    if expect.no_step:
        assert step is None, f"{where}: unexpected scenario_step {step}"
