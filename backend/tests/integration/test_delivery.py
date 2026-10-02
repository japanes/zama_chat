"""Result delivery: queue_delivery + the deliver job against httpx.MockTransport."""

import json
import uuid
from datetime import timedelta

import httpx
import pytest

from app.db.models import (
    Contact,
    Conversation,
    DeliveryStatus,
    JobStatus,
    RunStatus,
    ScenarioRun,
)
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from app.results.delivery import (
    EVENT_HEADER,
    IDEMPOTENCY_HEADER,
    MAX_ATTEMPTS,
    RETRY_DELAYS_S,
    SIGNATURE_HEADER,
    queue_delivery,
    resend,
    sign,
)
from app.scenarios.store import load_definition
from tests.factories import make_tenant
from tests.scenario_helpers import (
    deliveries_of,
    install_scenario,
    interview_data,
    jobs_like,
    make_def,
    make_endpoint,
    states,
)

pytestmark = pytest.mark.db

URL = "https://crm.example.com/hooks/zama"
SECRET = "s3cret"


class Receiver:
    """Answers with the scripted codes (the last one repeats); an Exception is raised."""

    def __init__(self, *answers):
        self.answers = list(answers) or [200]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, json={"ok": answer < 300})


@pytest.fixture
async def run_id(services):
    bundle = await make_tenant(services.sm)
    scenario_id = await install_scenario(
        services.sm, bundle.tenant_id, bundle.channel_id, interview_data()
    )
    await make_endpoint(
        services, bundle.tenant_id, url=URL, secret=SECRET, headers={"X-Api-Key": "k1"}
    )
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        contact = Contact(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            external_id="c1",
            username="olha",
            name="Olha",
        )
        session.add(contact)
        await session.flush()
        conv = Conversation(
            tenant_id=bundle.tenant_id, channel_id=bundle.channel_id, contact_id=contact.id
        )
        session.add(conv)
        await session.flush()
        run = ScenarioRun(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            conversation_id=conv.id,
            scenario_id=scenario_id,
            scenario_key="interview",
            scenario_version=1,
            status=RunStatus.COMPLETED,
            fields=states(make_def(), niche="торти", offer="торти", positioning="за добу"),
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


def _use(services, receiver: Receiver) -> None:
    services.http = httpx.AsyncClient(transport=httpx.MockTransport(receiver))


async def _queue(
    services, run_id, *, event="completed", status="completed", endpoint="results_api"
):
    async with services.sm() as session, session.begin():
        run = await session.get(ScenarioRun, run_id, with_for_update=True)
        defn = await load_definition(session, run.scenario_id)
        delivery = await queue_delivery(
            session,
            run,
            defn,
            endpoint_name=endpoint,
            event=event,
            status=status,
            now=services.clock.now(),
        )
        return delivery.id if delivery else None


@pytest.fixture
def worker(services):
    return Worker(services.sm, services.clock, HANDLERS, services=services)


@pytest.fixture
async def receiver(services):
    await services.http.aclose()
    rec = Receiver(200)
    _use(services, rec)
    return rec


async def test_sends_a_signed_post_with_the_endpoint_headers(services, worker, run_id, receiver):
    delivery_id = await _queue(services, run_id)
    assert await worker.run_due() == 1

    [request] = receiver.requests
    assert str(request.url) == URL
    assert request.headers["X-Api-Key"] == "k1"
    assert request.headers[IDEMPOTENCY_HEADER] == str(delivery_id)
    assert request.headers[EVENT_HEADER] == "scenario.completed"
    assert request.headers[SIGNATURE_HEADER] == sign(SECRET, request.content)
    body = json.loads(request.content)
    assert body["result_id"] == str(run_id)
    assert body["seq"] == 1
    assert body["fields"] == {"niche": "торти", "offer": "торти", "positioning": "за добу"}
    assert (body["client_language"], body["reply_language"]) == ("ru", "uk")
    assert body["client"]["username"] == "olha"

    [delivery] = await deliveries_of(services.sm, run_id)
    assert delivery.status == DeliveryStatus.SENT
    assert (delivery.attempts, delivery.last_code) == (1, 200)
    assert delivery.sent_at == services.clock.now()


async def test_5xx_is_retried_with_the_same_idempotency_key(services, worker, run_id, receiver):
    receiver.answers = [503, 200]
    await _queue(services, run_id)
    await worker.run_due()
    [delivery] = await deliveries_of(services.sm, run_id)
    assert delivery.status == DeliveryStatus.PENDING
    assert (delivery.attempts, delivery.last_code) == (1, 503)
    [job] = await jobs_like(services.sm, f"deliver:{delivery.id}")
    assert job.run_at == services.clock.now() + timedelta(seconds=RETRY_DELAYS_S[0])

    services.clock.advance(seconds=RETRY_DELAYS_S[0] - 1)
    assert await worker.run_due() == 0
    services.clock.advance(seconds=1)
    assert await worker.run_due() == 1
    [delivery] = await deliveries_of(services.sm, run_id)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.attempts == 2
    first, second = receiver.requests
    assert first.headers[IDEMPOTENCY_HEADER] == second.headers[IDEMPOTENCY_HEADER]
    assert first.content == second.content


async def test_network_error_and_429_are_retried(services, worker, run_id, receiver):
    receiver.answers = [httpx.ConnectError("down"), 429, 200]
    await _queue(services, run_id)
    for delay in (0, RETRY_DELAYS_S[0], RETRY_DELAYS_S[1]):
        services.clock.advance(seconds=delay)
        await worker.run_due()
    [delivery] = await deliveries_of(services.sm, run_id)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.attempts == 3


async def test_4xx_fails_at_once(services, worker, run_id, receiver):
    receiver.answers = [400]
    await _queue(services, run_id)
    await worker.run_due()
    [delivery] = await deliveries_of(services.sm, run_id)
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.last_code == 400
    [job] = await jobs_like(services.sm, f"deliver:{delivery.id}")
    assert job.status == JobStatus.DONE
    services.clock.advance(days=1)
    assert await worker.run_due() == 0
    assert len(receiver.requests) == 1


async def test_gives_up_after_the_last_attempt(services, worker, run_id, receiver):
    receiver.answers = [500]
    await _queue(services, run_id)
    for _ in range(MAX_ATTEMPTS):
        await worker.run_due()
        services.clock.advance(seconds=max(RETRY_DELAYS_S))
    [delivery] = await deliveries_of(services.sm, run_id)
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == MAX_ATTEMPTS
    assert len(receiver.requests) == MAX_ATTEMPTS
    assert await worker.run_due() == 0


async def test_a_new_version_cancels_the_waiting_old_one(services, worker, run_id, receiver):
    old = await _queue(services, run_id, event="silence", status="incomplete")
    new = await _queue(services, run_id)
    old_row, new_row = await deliveries_of(services.sm, run_id)
    assert (old_row.id, new_row.id) == (old, new)
    assert old_row.status == DeliveryStatus.CANCELLED
    assert (old_row.seq, new_row.seq) == (1, 2)
    [old_job] = await jobs_like(services.sm, f"deliver:{old}")
    assert old_job.status == JobStatus.CANCELLED

    await worker.run_due()
    [request] = receiver.requests
    assert json.loads(request.content)["seq"] == 2
    assert json.loads(request.content)["status"] == "completed"


async def test_a_sent_version_is_not_cancelled_by_a_newer_one(services, worker, run_id, receiver):
    await _queue(services, run_id, event="silence", status="incomplete")
    await worker.run_due()
    await _queue(services, run_id)
    await worker.run_due()
    first, second = await deliveries_of(services.sm, run_id)
    assert (first.status, second.status) == (DeliveryStatus.SENT, DeliveryStatus.SENT)
    assert [json.loads(r.content)["seq"] for r in receiver.requests] == [1, 2]


async def test_missing_endpoint_queues_nothing(services, run_id):
    assert await _queue(services, run_id, endpoint="nope") is None
    assert await deliveries_of(services.sm, run_id) == []
    async with services.sm() as session:
        run = await session.get(ScenarioRun, run_id)
        assert run.delivery_seq == 0


async def test_resend_makes_a_new_version_of_the_same_event(services, worker, run_id, receiver):
    first = await _queue(services, run_id)
    await worker.run_due()
    async with services.sm() as session, session.begin():
        again = await resend(session, first, now=services.clock.now())
        again_id = again.id
    await worker.run_due()
    rows = await deliveries_of(services.sm, run_id)
    assert [r.id for r in rows] == [first, again_id]
    assert [r.seq for r in rows] == [1, 2]
    bodies = [json.loads(r.content) for r in receiver.requests]
    assert {b["result_id"] for b in bodies} == {str(run_id)}
    assert [b["status"] for b in bodies] == ["completed", "completed"]


async def test_resend_of_an_unknown_delivery(services, run_id):
    async with services.sm() as session, session.begin():
        assert await resend(session, uuid.uuid4(), now=services.clock.now()) is None


async def test_a_crashed_worker_does_not_lose_the_delivery(services, worker, run_id, receiver):
    await _queue(services, run_id)
    # a worker took the job and died before finishing it
    crashed = Worker(services.sm, services.clock, HANDLERS, services=services, lock_seconds=60)
    assert len(await crashed.claim(services.clock.now(), 10)) == 1
    assert await worker.run_due() == 0
    services.clock.advance(seconds=61)
    assert await worker.run_due() == 1
    [delivery] = await deliveries_of(services.sm, run_id)
    assert delivery.status == DeliveryStatus.SENT


async def test_a_rolled_back_turn_queues_nothing(services, run_id):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with services.sm() as session, session.begin():
            run = await session.get(ScenarioRun, run_id, with_for_update=True)
            defn = await load_definition(session, run.scenario_id)
            await queue_delivery(
                session,
                run,
                defn,
                endpoint_name="results_api",
                event="completed",
                status="completed",
                now=services.clock.now(),
            )
            raise Boom
    assert await deliveries_of(services.sm, run_id) == []
    assert await jobs_like(services.sm, "deliver:") == []
