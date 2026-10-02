"""Dialogs in YAML (tests/dialogs/*.yaml) played through POST /v1/messages on the full
pipeline: the fake LLM gives the scripted rewrite and reply, FakeDecider the route.

A file:

  name: …
  tenant: {settings: {languages: [uk, en], reply_language: {…}}}
  decide: {service_type: none, wants_human: false}   # answers for every turn
  turns:
    - client: Привіт!
      rewrite: {question: [Привіт], language: uk}     # what the rewrite model returns
      decide: {service_type: greeting_only}           # this turn only, over the defaults
      reply: Вітаю!                                   # what the reply model returns
      expect:                                         # all optional
        reply: Вітаю!
        prompt: service_greeting_only                 # prompt the reply was made with
        route: {service_type: greeting_only}          # subset of turns.route
        handover: false
        client_language: uk
        reply_language: uk
        rewritten: Привіт

Unknown keys are an error, so a typo does not silently skip a check.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.core.pipeline import full_pipeline
from app.db.models import Conversation, LlmRole, Turn, TurnStatus
from app.decisions.fake import FakeDecider
from app.prompts.seed import seed_prompts
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant

pytestmark = pytest.mark.db

DIALOGS_DIR = Path(__file__).parent
ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rewrite(Strict):
    question: list[str]
    language: str | None = None


class Expect(Strict):
    reply: str | None = None
    prompt: str | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    handover: bool | None = None
    client_language: str | None = None
    reply_language: str | None = None
    rewritten: str | None = None


class DialogTurn(Strict):
    client: str
    rewrite: Rewrite
    decide: dict[str, Any] = Field(default_factory=dict)
    reply: str
    expect: Expect = Field(default_factory=Expect)


class TenantSpec(Strict):
    settings: dict[str, Any] = Field(default_factory=dict)


class Dialog(Strict):
    name: str
    tenant: TenantSpec = Field(default_factory=TenantSpec)
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

    for number, turn in enumerate(dialog.turns, 1):
        where = f"{path.name}, turn {number} ({turn.client!r})"
        decider.rules = {**dialog.decide, **turn.decide}
        fake_llm.push(
            chat_reply(json.dumps(turn.rewrite.model_dump(), ensure_ascii=False)),
            chat_reply(turn.reply),
        )
        resp = await client.post(
            "/v1/messages",
            json={"contact": {"external_id": "c1", "name": "Olha"}, "text": turn.client},
            headers=bundle.auth,
        )
        assert resp.status_code == 200, f"{where}: {resp.text}"
        await _check(services, resp.json(), turn.expect, where)

    # every scripted LLM answer was used
    assert not fake_llm.script, f"{path.name}: unused LLM answers"


async def _check(services, body: dict, expect: Expect, where: str) -> None:
    async with services.sm() as session:
        turn = await session.get(Turn, uuid.UUID(body["turn_id"]))
        conv = await session.get(Conversation, uuid.UUID(body["conversation_id"]))
    assert turn is not None and conv is not None
    assert turn.status == TurnStatus.DONE, f"{where}: {turn.error}"

    if expect.reply is not None:
        assert body["reply"] == expect.reply, where
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
