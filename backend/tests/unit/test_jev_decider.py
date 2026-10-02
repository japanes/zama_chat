"""JevDecider on a fake Gateway: parsing, splitting, retries, fallback, thresholds, logs."""

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.db.models import BelowAction, DecisionProvider, DecisionReason, PolicyMode
from app.decisions import jev_decider
from app.decisions.engine import (
    Answer,
    DecideContext,
    DecisionSet,
    DecisionsUnavailable,
    decision_row,
)
from app.decisions.jev_decider import JevDecider, parse_answer
from app.decisions.limits import Limits
from app.decisions.policy import DecisionPolicy, Rule
from app.errors import AppError
from app.settings import Settings
from tests.decision_helpers import choice, score, yes_no

KEY = "secret-xyz"
CTX = DecideContext(tenant_id=uuid.uuid4(), turn_id=uuid.uuid4())


# --- fakes -------------------------------------------------------------------------------


class Gateway:
    """MockTransport handler. Each item: (status, json) | Exception | callable(body)."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(body)
        status, data = item
        return httpx.Response(status, json=data)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def ok(answers: dict[str, Any], *, cost: str = "0.00012", gen: str = "gen-1") -> tuple:
    return 200, {
        "answers": answers,
        "usage": {"inputTokens": 50, "outputTokens": 3},
        "providerMetadata": {"gateway": {"cost": cost, "generationId": gen}},
    }


def answer_everything(body: dict[str, Any]) -> tuple:
    """Sure answers to whatever was asked: boolean → true, choice → the first option."""
    answers = {}
    for key, q in body["questions"].items():
        if q["type"] == "boolean":
            answers[key] = {"probability": 0.97}
        else:
            first = next(iter(q["criteria"]))
            answers[key] = {"choice": first, "probabilities": {first: 0.95}}
    return ok(answers)


class FakeFallback:
    def __init__(self, values: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.values = values or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    async def decide(self, state, questions, ctx) -> DecisionSet:
        self.calls.append([q.key for q in questions])
        if self.fail:
            raise AppError("llm.unavailable")
        result = DecisionSet()
        batch_id = uuid.uuid4()
        for q in questions:
            answer = Answer(
                q.key, q.type, self.values[q.key], 0.8, DecisionProvider.LLM, "openai/test"
            )
            result.answers[q.key] = answer
            result.rows.append(
                decision_row(
                    q,
                    ctx=ctx,
                    batch_id=batch_id,
                    provider=DecisionProvider.LLM,
                    model="openai/test",
                    answer=answer,
                )
            )
        return result


class RecordingLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str):
        def record(event: str, **kw: Any) -> None:
            self.events.append((level, event, kw))

        return record

    def names(self) -> list[str]:
        return [event for _, event, _ in self.events]


@pytest.fixture
def jlog(monkeypatch) -> RecordingLog:
    rec = RecordingLog()
    monkeypatch.setattr(jev_decider, "log", rec)
    return rec


def make_decider(gateway: Gateway, **kw: Any) -> JevDecider:
    http = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    settings = Settings(_env_file=None, ai_gateway_api_key=KEY)
    return JevDecider(http, settings, retry_delay_s=0, **kw)


def by_key(rows):
    return {(r.key, r.provider): r for r in rows}


# --- parse_answer ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p", "value", "confidence"),
    [(0.98, True, 0.98), (0.02, False, 0.98), (0.5, True, 0.5), (0.3, False, 0.7)],
)
def test_parse_boolean(p, value, confidence):
    got_value, got_conf, probs = parse_answer({"probability": p})
    assert got_value is value
    assert got_conf == pytest.approx(confidence)
    assert probs == {}


def test_parse_choice_and_score():
    assert parse_answer({"choice": "a", "probabilities": {"a": 0.7, "b": 0.3}}) == (
        "a",
        0.7,
        {"a": 0.7, "b": 0.3},
    )
    value, conf, probs = parse_answer({"score": 3, "probabilities": {"3": 0.6, "4": "x"}})
    assert (value, conf, probs) == (3, 0.6, {"3": 0.6})


def test_parse_nothing_usable():
    assert parse_answer({}) == (None, 0.0, {})
    assert parse_answer({"choice": None, "probabilities": {"a": 1}}) == (None, 0.0, {"a": 1.0})


# --- request -----------------------------------------------------------------------------


async def test_request_body_url_and_auth(jlog):
    gateway = Gateway(answer_everything)
    decider = make_decider(gateway)
    questions = [yes_no("wants_human"), choice("service_type", "none", "greeting_only")]
    await decider.decide("STATE", questions, CTX)

    request = gateway.requests[0]
    assert str(request.url) == decider.url
    assert decider.url.endswith("/evaluate")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert gateway.bodies()[0] == {
        "model": "typesafe-ai/jev",
        "state": "STATE",
        "questions": {q.key: q.to_jev() for q in questions},
        "providerOptions": {"gateway": {"only": ["typesafe-ai"], "zeroDataRetention": True}},
    }


async def test_sure_answers_are_used_as_is(jlog):
    gateway = Gateway(
        ok(
            {
                "wants_human": {"probability": 0.03},
                "service_type": {
                    "choice": "greeting_only",
                    "probabilities": {"greeting_only": 0.91, "none": 0.09},
                },
                "rate": {"score": 2, "probabilities": {"2": 0.8}},
            },
            cost="0.0005",
            gen="gen-42",
        )
    )
    fallback = FakeFallback()
    decider = make_decider(gateway, fallback=fallback)
    questions = [
        yes_no("wants_human"),
        choice("service_type", "none", "greeting_only"),
        score("rate", 3),
    ]
    result = await decider.decide("s", questions, CTX)

    assert result.value("wants_human") is False
    assert result.answers["wants_human"].confidence == pytest.approx(0.97)
    assert result.value("service_type") == "greeting_only"
    assert result.value("rate") == 2
    assert all(a.provider == DecisionProvider.JEV for a in result.answers.values())
    assert all(a.reason == DecisionReason.PRIMARY for a in result.answers.values())
    assert fallback.calls == []
    assert len(gateway.requests) == 1

    rows = result.rows
    assert [r.key for r in rows] == ["wants_human", "service_type", "rate"]
    assert all(r.used and r.error is None for r in rows)
    assert len({r.batch_id for r in rows}) == 1
    # call cost and tokens on the first row only, generationId on all
    assert (rows[0].cost, rows[0].tokens_in, rows[0].tokens_out) == (Decimal("0.0005"), 50, 3)
    assert all(r.cost == 0 and r.tokens_in == 0 for r in rows[1:])
    assert {r.generation_id for r in rows} == {"gen-42"}
    assert rows[1].answer["probabilities"] == {"greeting_only": 0.91, "none": 0.09}
    assert "jev.answered" in jlog.names()


async def test_questions_over_the_limit_go_in_several_requests(jlog):
    gateway = Gateway(answer_everything)
    decider = make_decider(gateway, limits=Limits(max_questions=2))
    questions = [yes_no(f"q{i}") for i in range(5)]
    result = await decider.decide("s", questions, CTX)

    assert [list(b["questions"]) for b in gateway.bodies()] == [
        ["q0", "q1"],
        ["q2", "q3"],
        ["q4"],
    ]
    assert set(result.answers) == {f"q{i}" for i in range(5)}
    assert len({r.batch_id for r in result.rows}) == 3


async def test_no_questions_no_request(jlog):
    gateway = Gateway(answer_everything)
    result = await make_decider(gateway).decide("s", [], CTX)
    assert result.answers == {}
    assert gateway.requests == []


# --- errors ------------------------------------------------------------------------------


async def test_400_is_not_retried_and_goes_to_fallback(jlog):
    gateway = Gateway((400, {"error": {"message": "criteria key is invalid"}}))
    fallback = FakeFallback({"wants_human": True})
    decider = make_decider(gateway, fallback=fallback)
    result = await decider.decide("s", [yes_no("wants_human")], CTX)

    assert len(gateway.requests) == 1
    assert "jev.request_rejected" in jlog.names()
    assert "jev.retry" not in jlog.names()
    assert fallback.calls == [["wants_human"]]
    answer = result.answers["wants_human"]
    assert (answer.value, answer.provider, answer.reason) == (
        True,
        DecisionProvider.LLM,
        DecisionReason.JEV_ERROR,
    )
    rows = by_key(result.rows)
    jev_row = rows[("wants_human", "jev")]
    assert jev_row.used is False
    assert jev_row.error == "400: criteria key is invalid"
    assert jev_row.reason == "primary"
    assert rows[("wants_human", "llm")].reason == "jev_error"


async def test_400_without_fallback_is_unavailable(jlog):
    gateway = Gateway((400, {"error": {"message": "bad"}}))
    decider = make_decider(gateway)
    with pytest.raises(DecisionsUnavailable) as err:
        await decider.decide("s", [yes_no("a"), yes_no("b")], CTX)
    assert err.value.params == {"keys": ["a", "b"]}
    assert err.value.status == 503
    assert [r.key for r in err.value.rows] == ["a", "b"]
    assert all(r.used is False for r in err.value.rows)


async def test_502_is_retried_once_then_succeeds(jlog):
    gateway = Gateway((502, {"error": {"message": "upstream"}}), answer_everything)
    fallback = FakeFallback()
    result = await make_decider(gateway, fallback=fallback).decide("s", [yes_no("a")], CTX)

    assert len(gateway.requests) == 2
    assert jlog.names().count("jev.retry") == 1
    assert result.answers["a"].provider == DecisionProvider.JEV
    assert fallback.calls == []


@pytest.mark.parametrize("status", [429, 408, 500, 503])
async def test_temporary_statuses_are_retried(jlog, status):
    gateway = Gateway((status, {}), answer_everything)
    await make_decider(gateway).decide("s", [yes_no("a")], CTX)
    assert len(gateway.requests) == 2


async def test_502_twice_goes_to_fallback_with_jev_error(jlog):
    gateway = Gateway((502, {"error": {"message": "upstream"}}))
    fallback = FakeFallback({"a": True, "b": "x"})
    decider = make_decider(gateway, fallback=fallback)
    result = await decider.decide("s", [yes_no("a"), choice("b", "x", "y")], CTX)

    assert len(gateway.requests) == 2
    assert "jev.failed" in jlog.names()
    assert fallback.calls == [["a", "b"]]
    assert {k: a.reason for k, a in result.answers.items()} == {
        "a": DecisionReason.JEV_ERROR,
        "b": DecisionReason.JEV_ERROR,
    }
    jev_rows = [r for r in result.rows if r.provider == "jev"]
    llm_rows = [r for r in result.rows if r.provider == "llm"]
    assert all(not r.used and r.error.startswith("502:") for r in jev_rows)
    assert all(r.used and r.reason == "jev_error" for r in llm_rows)


async def test_network_error_is_status_zero_and_retried(jlog):
    gateway = Gateway(httpx.ConnectError("boom"))
    fallback = FakeFallback({"a": False})
    result = await make_decider(gateway, fallback=fallback).decide("s", [yes_no("a")], CTX)

    assert len(gateway.requests) == 2
    jev_row = by_key(result.rows)[("a", "jev")]
    assert jev_row.error == "ConnectError"
    assert result.value("a") is False


async def test_answer_that_does_not_fit_is_not_used(jlog):
    gateway = Gateway(ok({"b": {"choice": "zzz", "probabilities": {"zzz": 0.99}}}))
    fallback = FakeFallback({"a": True, "b": "x"})
    result = await make_decider(gateway, fallback=fallback).decide(
        "s", [yes_no("a"), choice("b", "x", "y")], CTX
    )
    # "a" missing in the answer, "b" picked an option that does not exist
    assert fallback.calls == [["a", "b"]]
    rows = by_key(result.rows)
    assert rows[("a", "jev")].error == "no usable answer"
    assert rows[("b", "jev")].error == "no usable answer"
    assert rows[("b", "jev")].used is False
    assert jlog.names().count("jev.no_answer") == 2


# --- threshold ---------------------------------------------------------------------------


async def test_only_unsure_questions_go_to_fallback(jlog):
    gateway = Gateway(
        ok(
            {
                "wants_human": {"probability": 0.6},  # confidence 0.6 < 0.7
                "service_type": {"choice": "none", "probabilities": {"none": 0.9}},
            }
        )
    )
    fallback = FakeFallback({"wants_human": True})
    decider = make_decider(gateway, fallback=fallback, policy=DecisionPolicy())
    result = await decider.decide(
        "s", [yes_no("wants_human"), choice("service_type", "none", "greeting_only")], CTX
    )

    assert fallback.calls == [["wants_human"]]
    assert result.answers["wants_human"].provider == DecisionProvider.LLM
    assert result.answers["wants_human"].reason == DecisionReason.BELOW_THRESHOLD
    assert result.answers["service_type"].provider == DecisionProvider.JEV
    rows = by_key(result.rows)
    assert rows[("wants_human", "jev")].used is False
    assert rows[("wants_human", "jev")].answer == {"value": True, "probability": 0.6}
    assert rows[("service_type", "jev")].used is True
    assert rows[("wants_human", "llm")].reason == "below_threshold"


async def test_threshold_by_key(jlog):
    gateway = Gateway(ok({"start:x.y": {"probability": 0.85}}))
    fallback = FakeFallback({"start:x.y": False})
    policy = DecisionPolicy(thresholds={"start:*": 0.9})
    result = await make_decider(gateway, fallback=fallback, policy=policy).decide(
        "s", [yes_no("start:x.y")], CTX
    )
    assert fallback.calls == [["start:x.y"]]
    assert result.value("start:x.y") is False


async def test_fallback_failure_keeps_unsure_jev_answer(jlog):
    gateway = Gateway(ok({"a": {"probability": 0.6}}))
    decider = make_decider(gateway, fallback=FakeFallback(fail=True))
    result = await decider.decide("s", [yes_no("a")], CTX)

    assert "decisions.fallback_failed" in jlog.names()
    answer = result.answers["a"]
    assert (answer.value, answer.provider) == (True, DecisionProvider.JEV)
    assert result.rows[0].used is True


async def test_fallback_failure_reports_only_failed_keys(jlog):
    gateway = Gateway(ok({"unsure": {"probability": 0.4}}))  # "lost" has no answer
    decider = make_decider(gateway, fallback=FakeFallback(fail=True))
    with pytest.raises(DecisionsUnavailable) as err:
        await decider.decide("s", [yes_no("unsure"), yes_no("lost")], CTX)
    assert err.value.params == {"keys": ["lost"]}
    assert {r.key for r in err.value.rows} == {"unsure", "lost"}


# --- the key never leaks -----------------------------------------------------------------


async def test_gateway_key_is_not_in_logs_or_rows(jlog):
    gateway = Gateway(
        (400, {"error": {"message": "nope"}}),
        (502, {}),
        httpx.ConnectError("boom"),
        ok({"c": {"probability": 0.55}}),
    )
    decider = make_decider(gateway, fallback=FakeFallback({"a": True, "b": True, "c": True}))
    rows = []
    for key in ("a", "b", "c"):
        rows += (await decider.decide("s", [yes_no(key)], CTX)).rows

    assert KEY not in repr(jlog.events)
    for row in rows:
        stored = [row.error, row.answer, row.question, row.model, row.generation_id]
        assert KEY not in json.dumps(stored, default=str)


# --- modes and below-threshold actions (stage 7) -----------------------------------------


def ctx_with(**rules: Rule) -> DecideContext:
    """A context whose policy has these rules (by their patterns)."""
    return DecideContext(
        tenant_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        policy=DecisionPolicy(rules={r.pattern: r for r in rules.values()}),
    )


def rule_for(pattern: str, mode=PolicyMode.JEV, threshold=0.7, below=BelowAction.LLM) -> Rule:
    return Rule(pattern, mode, threshold, below)


async def test_shadow_sure_jev_is_used_llm_is_logged_only(jlog):
    gateway = Gateway(ok({"a": {"probability": 0.95}}))
    fallback = FakeFallback({"a": False})
    ctx = ctx_with(a=rule_for("a", PolicyMode.SHADOW))
    result = await make_decider(gateway, fallback=fallback).decide("s", [yes_no("a")], ctx)

    assert fallback.calls == [["a"]]  # asked together with Jev, not after
    answer = result.answers["a"]
    assert (answer.value, answer.provider, answer.reason) == (
        True,
        DecisionProvider.JEV,
        DecisionReason.PRIMARY,
    )
    rows = by_key(result.rows)
    assert rows[("a", "jev")].used is True
    assert (rows[("a", "llm")].used, rows[("a", "llm")].reason) == (False, "shadow")


async def test_shadow_answer_is_reused_when_jev_is_unsure(jlog):
    gateway = Gateway(ok({"a": {"probability": 0.6}}))
    fallback = FakeFallback({"a": False})
    ctx = ctx_with(a=rule_for("a", PolicyMode.SHADOW))
    result = await make_decider(gateway, fallback=fallback).decide("s", [yes_no("a")], ctx)

    assert fallback.calls == [["a"]]  # no second LLM call
    answer = result.answers["a"]
    assert (answer.value, answer.provider, answer.reason) == (
        False,
        DecisionProvider.LLM,
        DecisionReason.BELOW_THRESHOLD,
    )
    rows = by_key(result.rows)
    assert rows[("a", "jev")].used is False
    assert (rows[("a", "llm")].used, rows[("a", "llm")].reason) == (True, "below_threshold")


async def test_shadow_answer_is_reused_when_jev_fails(jlog):
    gateway = Gateway((400, {"error": {"message": "bad"}}))
    fallback = FakeFallback({"a": True})
    ctx = ctx_with(a=rule_for("a", PolicyMode.SHADOW))
    result = await make_decider(gateway, fallback=fallback).decide("s", [yes_no("a")], ctx)

    assert fallback.calls == [["a"]]
    assert result.answers["a"].reason == DecisionReason.JEV_ERROR
    assert by_key(result.rows)[("a", "llm")].used is True


async def test_mode_llm_does_not_ask_jev(jlog):
    gateway = Gateway(answer_everything)
    fallback = FakeFallback({"a": True})
    ctx = ctx_with(a=rule_for("a", PolicyMode.LLM), b=rule_for("b"))
    result = await make_decider(gateway, fallback=fallback).decide(
        "s", [yes_no("a"), yes_no("b")], ctx
    )

    assert [list(b["questions"]) for b in gateway.bodies()] == [["b"]]
    assert fallback.calls == [["a"]]
    assert result.answers["a"].provider == DecisionProvider.LLM
    assert result.answers["a"].reason == DecisionReason.PRIMARY
    assert result.answers["b"].provider == DecisionProvider.JEV
    assert by_key(result.rows)[("a", "llm")].used is True


async def test_mode_llm_failed_goes_to_jev_with_llm_error(jlog):
    gateway = Gateway(ok({"a": {"probability": 0.55}}))
    ctx = ctx_with(a=rule_for("a", PolicyMode.LLM))
    decider = make_decider(gateway, fallback=FakeFallback(fail=True))
    result = await decider.decide("s", [yes_no("a")], ctx)

    assert "decisions.llm_failed" in jlog.names()
    answer = result.answers["a"]
    # Jev answers whatever its confidence: there is nobody else to ask
    assert (answer.value, answer.provider, answer.reason) == (
        True,
        DecisionProvider.JEV,
        DecisionReason.LLM_ERROR,
    )
    assert by_key(result.rows)[("a", "jev")].reason == "llm_error"


async def test_mode_llm_without_any_answer_is_unavailable(jlog):
    gateway = Gateway((400, {"error": {"message": "bad"}}))
    ctx = ctx_with(a=rule_for("a", PolicyMode.LLM))
    decider = make_decider(gateway, fallback=FakeFallback(fail=True))
    with pytest.raises(DecisionsUnavailable) as err:
        await decider.decide("s", [yes_no("a")], ctx)
    assert err.value.params == {"keys": ["a"]}


async def test_below_keep_uses_the_unsure_jev_answer(jlog):
    gateway = Gateway(ok({"a": {"probability": 0.6}}))
    fallback = FakeFallback({"a": False})
    ctx = ctx_with(a=rule_for("a", below=BelowAction.KEEP))
    result = await make_decider(gateway, fallback=fallback).decide("s", [yes_no("a")], ctx)

    assert fallback.calls == []
    assert result.value("a") is True
    assert result.answers["a"].reason == DecisionReason.PRIMARY
    assert by_key(result.rows)[("a", "jev")].used is True


@pytest.mark.parametrize("below", [BelowAction.ASK_CLIENT, BelowAction.OPERATOR])
async def test_below_ask_client_or_operator_marks_the_answer_unsure(jlog, below):
    gateway = Gateway(
        ok({"a": {"probability": 0.6}, "b": {"probability": 0.99}})  # a unsure, b sure
    )
    fallback = FakeFallback({"a": False})
    ctx = ctx_with(a=rule_for("a", below=below), b=rule_for("b", below=below))
    result = await make_decider(gateway, fallback=fallback).decide(
        "s", [yes_no("a"), yes_no("b")], ctx
    )

    assert fallback.calls == []
    a = result.answers["a"]
    assert (a.unsure, a.below, a.reason) == (True, str(below), DecisionReason.UNSURE)
    assert result.value("a", "default") == "default"  # unsure = no answer for the turn
    assert result.is_unsure("a") and not result.is_unsure("b")
    assert result.value("b") is True
    row = by_key(result.rows)[("a", "jev")]
    assert (row.used, row.reason) == (False, "unsure")
    expected = ["a"] if below == BelowAction.OPERATOR else []
    assert result.operator_keys == expected


async def test_threshold_of_the_rule_is_used(jlog):
    gateway = Gateway(ok({"a": {"probability": 0.85}}))
    fallback = FakeFallback({"a": False})
    ctx = ctx_with(a=rule_for("a", threshold=0.9))
    result = await make_decider(gateway, fallback=fallback).decide("s", [yes_no("a")], ctx)
    assert fallback.calls == [["a"]]
    assert result.answers["a"].reason == DecisionReason.BELOW_THRESHOLD


async def test_ctx_policy_wins_over_the_decider_policy(jlog):
    gateway = Gateway(ok({"a": {"probability": 0.85}}))
    fallback = FakeFallback({"a": False})
    decider = make_decider(
        gateway, fallback=fallback, policy=DecisionPolicy(default_threshold=0.99)
    )
    result = await decider.decide("s", [yes_no("a")], ctx_with(a=rule_for("a", threshold=0.8)))
    assert fallback.calls == []
    assert result.value("a") is True
