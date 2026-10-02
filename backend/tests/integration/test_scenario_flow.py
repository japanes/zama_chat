"""Scenarios over POST /v1/messages on the full pipeline: the fake LLM gives rewrite,
extract and reply in that order, FakeDecider the route and the filters. Results go to
the dev sink of the same app; timers run with the manual clock and Worker.run_due()."""

import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.pipeline import full_pipeline
from app.db.models import DeliveryStatus, JobStatus, LlmRole, RunStatus
from app.decisions.fake import FakeDecider
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from app.main import create_app
from app.prompts.seed import seed_prompts
from app.results.delivery import (
    EVENT_HEADER,
    IDEMPOTENCY_HEADER,
    SIGNATURE_HEADER,
    encode_body,
    sign,
)
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import (
    ROUTE_DEFAULTS,
    booking_data,
    deliveries_of,
    install_scenario,
    interview_data,
    jobs_like,
    make_endpoint,
    runs_of,
)

pytestmark = pytest.mark.db

ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE, LlmRole.EXTRACT)
LANGUAGES = {"languages": ["uk", "en"], "reply_language": {"uk": "uk", "ru": "uk", "*": "en"}}
SECRET = "s3cret"
SINK = "http://test/dev/sink/results"

START = {"start:interview.wants_help": True, "field:interview.niche": True}
FIRST = "Хочу розібратися з позиціонуванням. Я печу торти на замовлення"
FIRST_EXTRACT = {
    "interview__niche": "торти на замовлення",
    "interview__offer": None,
    "interview__positioning": None,
}
SECOND = "Продаю весільні торти, роблю їх за добу"
SECOND_ROUTE = {"field:interview.offer": True, "field:interview.positioning": True}
SECOND_EXTRACT = {
    "interview__offer": "весільні торти",
    "interview__positioning": "роблю їх за добу",
}


async def _setup(services, *scenarios):
    decider = FakeDecider(dict(ROUTE_DEFAULTS))
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = decider
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES)
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    for data in scenarios:
        await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, data)
    # deliveries reach the dev sink of the same app, no network
    await services.http.aclose()
    services.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)))
    await make_endpoint(services, bundle.tenant_id, url=SINK, secret=SECRET)
    worker = Worker(services.sm, services.clock, HANDLERS, services=services)
    return SimpleNamespace(bundle=bundle, decider=decider, worker=worker)


@pytest.fixture
async def env(services):
    return await _setup(services, interview_data())


async def _say(client, env, fake_llm, text, *, route=None, extract=None, reply="Добре."):
    env.decider.rules = {**ROUTE_DEFAULTS, **(route or {})}
    script = [chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False))]
    if extract is not None:
        script.append(chat_reply(json.dumps(extract, ensure_ascii=False)))
    script.append(chat_reply(reply))
    fake_llm.push(*script)
    resp = await client.post(
        "/v1/messages",
        json={"contact": {"external_id": "c1", "name": "Olha"}, "text": text},
        headers=env.bundle.auth,
    )
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    return resp.json()


def _scenario_step(fake_llm) -> dict:
    return json.loads(fake_llm.requests[-1]["messages"][-1]["content"])["scenario_step"]


async def _sink(client) -> list[dict]:
    resp = await client.get("/dev/sink/results")
    assert resp.status_code == 200
    return list(reversed(resp.json()["items"]))  # oldest first


async def test_full_interview_sends_the_signed_result(client, services, fake_llm, env):
    body = await _say(client, env, fake_llm, FIRST, route=START, extract=FIRST_EXTRACT)
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.status == RunStatus.FOCUS
    assert run.asking == "offer"
    assert run.fields["niche"]["state"] == "filled"
    assert run.fields["niche"]["value"] == "торти на замовлення"
    assert (run.client_language, run.reply_language) == ("uk", "uk")
    step = _scenario_step(fake_llm)
    assert step["ask_next"] == {"question": "Що ви продаєте?"}
    assert step["scenario_started"] == "Інтерв'ю"
    assert env.decider.asked().count("filter:interview.niche.concrete") == 1

    [silence] = await jobs_like(services.sm, f"silence:{run.id}:")
    assert silence.run_at == services.clock.now() + timedelta(minutes=15)
    assert silence.status == JobStatus.QUEUED

    body = await _say(client, env, fake_llm, SECOND, route=SECOND_ROUTE, extract=SECOND_EXTRACT)
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.status == RunStatus.COMPLETED
    assert run.finished_at is not None
    assert _scenario_step(fake_llm)["scenario_completed"] == "Інтерв'ю"
    assert [j.status for j in await jobs_like(services.sm, f"silence:{run.id}:")] == [
        JobStatus.CANCELLED
    ]

    await env.worker.run_due()
    [delivery] = await deliveries_of(services.sm, run.id)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.seq == 1

    [item] = await _sink(client)
    sent = item["body"]
    assert sent["event"] == "scenario.completed"
    assert sent["status"] == "completed"
    assert sent["result_id"] == str(run.id)
    assert sent["fields"] == {
        "niche": "торти на замовлення",
        "offer": "весільні торти",
        "positioning": "роблю їх за добу",
    }
    assert sent["missing"] == []
    assert sent["client"]["id"] == "c1"
    assert item["headers"][IDEMPOTENCY_HEADER] == str(delivery.id)
    assert item["headers"][EVENT_HEADER] == "scenario.completed"
    assert item["headers"][SIGNATURE_HEADER] == sign(SECRET, encode_body(delivery.payload))


async def test_silence_sends_a_partial_result_then_the_full_one(client, services, fake_llm, env):
    body = await _say(client, env, fake_llm, FIRST, route=START, extract=FIRST_EXTRACT)
    [run] = await runs_of(services.sm, body["conversation_id"])

    services.clock.advance(minutes=14)
    await env.worker.run_due()
    assert await _sink(client) == []

    services.clock.advance(minutes=1)
    await env.worker.run_due()
    [partial] = await _sink(client)
    assert partial["body"]["status"] == "incomplete"
    assert partial["body"]["event"] == "scenario.silence"
    assert partial["body"]["fields"] == {"niche": "торти на замовлення"}
    assert partial["body"]["missing"] == ["offer", "positioning"]
    assert partial["body"]["seq"] == 1
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.status == RunStatus.FOCUS  # silence closes nothing

    services.clock.advance(hours=3)
    await _say(client, env, fake_llm, SECOND, route=SECOND_ROUTE, extract=SECOND_EXTRACT)
    await env.worker.run_due()
    first, full = await _sink(client)
    assert first == partial
    assert full["body"]["status"] == "completed"
    assert full["body"]["result_id"] == partial["body"]["result_id"]
    assert full["body"]["seq"] == 2


async def test_a_client_message_moves_the_silence_timer(client, services, fake_llm, env):
    await _say(client, env, fake_llm, FIRST, route=START, extract=FIRST_EXTRACT)
    services.clock.advance(minutes=10)
    await _say(client, env, fake_llm, "а що далі?", route={"client_question": True})
    services.clock.advance(minutes=10)  # 20 min after the first, 10 after the second
    await env.worker.run_due()
    assert await _sink(client) == []
    services.clock.advance(minutes=5)
    await env.worker.run_due()
    assert len(await _sink(client)) == 1


async def test_side_question_is_answered_and_the_bot_returns(client, services, fake_llm, env):
    await _say(client, env, fake_llm, FIRST, route=START, extract=FIRST_EXTRACT)
    await _say(
        client,
        env,
        fake_llm,
        "А скільки коштує ваша консультація?",
        route={"client_question": True},
    )
    step = _scenario_step(fake_llm)
    assert step["answer_client_question_first"] is True
    assert step["ask_next"] == {"question": "Що ви продаєте?"}


async def test_a_failed_filter_is_clarified_and_the_answer_merged(client, services, fake_llm, env):
    body = await _say(
        client,
        env,
        fake_llm,
        "Хочу розібратися з позиціонуванням, я в бʼюті",
        route={**START, "filter:interview.niche.concrete": False},
        extract={**FIRST_EXTRACT, "interview__niche": "бʼюті"},
    )
    clarify = _scenario_step(fake_llm)["clarify"]
    assert clarify["client_answer"] == "бʼюті"
    assert clarify["missing"] == ["Ніша конкретна?"]

    await _say(
        client,
        env,
        fake_llm,
        "манікюр і педикюр",
        route={"field:interview.niche": True},
        extract={"interview__niche": "манікюр і педикюр"},
    )
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.fields["niche"]["state"] == "filled"
    assert run.fields["niche"]["value"] == "бʼюті\nманікюр і педикюр"
    assert run.asking == "offer"


async def test_a_retold_value_is_not_saved(client, services, fake_llm, env):
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route=START,
        extract={**FIRST_EXTRACT, "interview__niche": "кондитерський бізнес преміум-класу"},
    )
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.fields["niche"]["value"] is None
    assert run.asking == "niche"


async def test_cancel_sends_a_cancelled_result(client, services, fake_llm):
    actions = [{"on": "cancelled", "do": "http_post", "endpoint": "results_api"}]
    env = await _setup(services, interview_data(actions=actions))
    body = await _say(client, env, fake_llm, FIRST, route=START, extract=FIRST_EXTRACT)
    await _say(client, env, fake_llm, "Ні, передумала", route={"stop": "cancel:interview"})
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.status == RunStatus.CANCELLED
    assert _scenario_step(fake_llm)["scenario_cancelled"] == "Інтерв'ю"
    await env.worker.run_due()
    [item] = await _sink(client)
    assert item["body"]["status"] == "cancelled"
    assert item["body"]["fields"] == {"niche": "торти на замовлення"}


async def test_a_second_scenario_waits_on_pause_and_takes_the_focus(client, services, fake_llm):
    env = await _setup(services, interview_data(), booking_data())
    body = await _say(client, env, fake_llm, FIRST, route=START, extract=FIRST_EXTRACT)
    await _say(
        client,
        env,
        fake_llm,
        "і ще хочу записатися",
        route={"start:booking.wants_booking": True},
        extract={"booking__service": None, "booking__day": None},
    )
    runs = {r.scenario_key: r for r in await runs_of(services.sm, body["conversation_id"])}
    assert runs["interview"].status == RunStatus.FOCUS
    assert runs["booking"].status == RunStatus.PAUSED

    await _say(client, env, fake_llm, SECOND, route=SECOND_ROUTE, extract=SECOND_EXTRACT)
    runs = {r.scenario_key: r for r in await runs_of(services.sm, body["conversation_id"])}
    assert runs["interview"].status == RunStatus.COMPLETED
    assert runs["booking"].status == RunStatus.FOCUS
    assert runs["booking"].asking == "service"
    assert _scenario_step(fake_llm)["ask_next"] == {"question": "Яка послуга?"}


async def test_within_hours_abandons_and_a_new_request_starts_over(client, services, fake_llm):
    booking = booking_data(
        resume={"mode": "within_hours", "hours": 24},
        actions=[{"on": "abandoned", "do": "http_post", "endpoint": "results_api"}],
    )
    env = await _setup(services, booking)
    want = {"start:booking.wants_booking": True}
    body = await _say(
        client,
        env,
        fake_llm,
        "Хочу записатися на манікюр",
        route=want,
        extract={"booking__service": "манікюр", "booking__day": None},
    )
    [first] = await runs_of(services.sm, body["conversation_id"])
    assert first.fields["service"]["value"] == "манікюр"
    [expire] = await jobs_like(services.sm, f"expire:{first.id}")
    assert expire.status == JobStatus.QUEUED

    services.clock.advance(hours=24)
    await env.worker.run_due()
    [old] = await runs_of(services.sm, body["conversation_id"])
    assert old.status == RunStatus.ABANDONED
    [item] = await _sink(client)
    assert item["body"]["status"] == "abandoned"
    assert item["body"]["fields"] == {"service": "манікюр"}

    await _say(
        client,
        env,
        fake_llm,
        "Хочу записатися на педикюр",
        route=want,
        extract={"booking__service": "педикюр", "booking__day": None},
    )
    old, new = await runs_of(services.sm, body["conversation_id"])
    assert new.id != old.id
    assert new.status == RunStatus.FOCUS
    assert new.fields["service"]["value"] == "педикюр"


async def test_an_expired_run_is_closed_by_the_turn_before_the_job(client, services, fake_llm):
    booking = booking_data(resume={"mode": "within_hours", "hours": 1})
    env = await _setup(services, booking)
    want = {"start:booking.wants_booking": True}
    extract = {"booking__service": None, "booking__day": None}
    body = await _say(client, env, fake_llm, "Хочу записатися", route=want, extract=extract)
    services.clock.advance(hours=2)
    # no worker run: the turn sees the window is over by itself
    await _say(client, env, fake_llm, "Хочу записатися", route=want, extract=extract)
    old, new = await runs_of(services.sm, body["conversation_id"])
    assert old.status == RunStatus.ABANDONED
    assert new.status == RunStatus.FOCUS
    await env.worker.run_due()  # the old expire job finds the run closed and does nothing
    [old, new] = await runs_of(services.sm, body["conversation_id"])
    assert old.status == RunStatus.ABANDONED
