"""Jobs queue on a real PostgreSQL: claim, retry/backoff, dedupe, two workers."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import Job, JobStatus
from app.jobs.queue import cancel, cancel_prefix, enqueue
from app.jobs.worker import Fail, JobContext, Retry, Worker

pytestmark = pytest.mark.db


async def _enqueue(sm, clock, kind="t", payload=None, **kw) -> Any:
    async with sm() as session, session.begin():
        return await enqueue(session, kind, payload, run_at=kw.pop("run_at", clock.now()), **kw)


async def _job(sm, job_id) -> Job:
    async with sm() as session:
        return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _jobs(sm) -> list[Job]:
    async with sm() as session:
        return list((await session.execute(select(Job).order_by(Job.created_at))).scalars())


async def test_claim_runs_due_job_and_marks_done(sm, clock):
    seen: list[JobContext] = []

    async def handler(ctx: JobContext) -> None:
        seen.append(ctx)

    job_id = await _enqueue(sm, clock, payload={"x": 1})
    worker = Worker(sm, clock, {"t": handler})
    assert await worker.run_due() == 1
    assert seen[0].payload == {"x": 1}
    assert seen[0].attempts == 1
    job = await _job(sm, job_id)
    assert job.status == JobStatus.DONE
    assert job.finished_at == clock.now()
    assert job.locked_until is None


async def test_future_job_is_not_claimed_until_due(sm, clock):
    calls = []

    async def handler(ctx):
        calls.append(ctx.id)

    await _enqueue(sm, clock, run_at=clock.now() + timedelta(minutes=5))
    worker = Worker(sm, clock, {"t": handler})
    assert await worker.run_due() == 0
    clock.advance(minutes=5)
    assert await worker.run_due() == 1
    assert len(calls) == 1


async def test_exception_retries_with_backoff(sm, clock):
    async def handler(ctx):
        raise RuntimeError("boom")

    job_id = await _enqueue(sm, clock)
    worker = Worker(sm, clock, {"t": handler}, backoff_s=(10, 60))
    await worker.run_due()
    job = await _job(sm, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.run_at == clock.now() + timedelta(seconds=10)
    assert "RuntimeError: boom" in (job.last_error or "")

    # not due yet
    assert await worker.run_due() == 0
    clock.advance(seconds=10)
    await worker.run_due()
    job = await _job(sm, job_id)
    assert job.attempts == 2
    assert job.run_at == clock.now() + timedelta(seconds=60)


async def test_retry_with_explicit_delay(sm, clock):
    async def handler(ctx):
        raise Retry(delay_s=2.5, reason="busy")

    job_id = await _enqueue(sm, clock)
    await Worker(sm, clock, {"t": handler}).run_due()
    job = await _job(sm, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.run_at == clock.now() + timedelta(seconds=2.5)
    assert job.last_error == "busy"


async def test_fail_is_permanent(sm, clock):
    async def handler(ctx):
        raise Fail("bad payload")

    job_id = await _enqueue(sm, clock, max_attempts=5)
    await Worker(sm, clock, {"t": handler}).run_due()
    job = await _job(sm, job_id)
    assert job.status == JobStatus.FAILED
    assert job.last_error == "bad payload"
    assert job.attempts == 1


async def test_unknown_kind_fails(sm, clock):
    job_id = await _enqueue(sm, clock, kind="nobody_handles_me")
    await Worker(sm, clock, {}).run_due()
    job = await _job(sm, job_id)
    assert job.status == JobStatus.FAILED
    assert "no handler" in (job.last_error or "")


async def test_last_attempt_fails(sm, clock):
    attempts: list[tuple[int, bool]] = []

    async def handler(ctx):
        attempts.append((ctx.attempts, ctx.is_last_attempt))
        raise RuntimeError("still broken")

    job_id = await _enqueue(sm, clock, max_attempts=2)
    worker = Worker(sm, clock, {"t": handler}, backoff_s=(1,))
    await worker.run_due()
    clock.advance(seconds=1)
    await worker.run_due()
    job = await _job(sm, job_id)
    assert job.status == JobStatus.FAILED
    assert attempts == [(1, False), (2, True)]
    clock.advance(hours=1)
    assert await worker.run_due() == 0


async def test_dedupe_reschedule_moves_run_at_and_payload(sm, clock):
    first = await _enqueue(sm, clock, payload={"v": 1}, dedupe_key="merge:c1")
    later = clock.now() + timedelta(seconds=30)
    second = await _enqueue(sm, clock, payload={"v": 2}, dedupe_key="merge:c1", run_at=later)
    assert first == second
    jobs = await _jobs(sm)
    assert len(jobs) == 1
    assert jobs[0].run_at == later
    assert jobs[0].payload == {"v": 2}


async def test_dedupe_keep_leaves_existing(sm, clock):
    first = await _enqueue(sm, clock, payload={"v": 1}, dedupe_key="k")
    second = await _enqueue(
        sm,
        clock,
        payload={"v": 2},
        dedupe_key="k",
        run_at=clock.now() + timedelta(hours=1),
        on_conflict="keep",
    )
    assert first == second
    job = await _job(sm, first)
    assert job.payload == {"v": 1}
    assert job.run_at == clock.now()


async def test_dedupe_earliest_keeps_earlier_run_at(sm, clock):
    early = clock.now() + timedelta(seconds=10)
    late = clock.now() + timedelta(seconds=60)
    job_id = await _enqueue(sm, clock, payload={"v": 1}, dedupe_key="k", run_at=late)
    await _enqueue(
        sm, clock, payload={"v": 2}, dedupe_key="k", run_at=early, on_conflict="earliest"
    )
    await _enqueue(sm, clock, payload={"v": 3}, dedupe_key="k", run_at=late, on_conflict="earliest")
    job = await _job(sm, job_id)
    assert job.run_at == early
    assert job.payload == {"v": 3}


async def test_running_job_is_not_rescheduled_new_one_is_queued(sm, clock):
    """A running job keeps its row; the same dedupe_key queues a second job."""
    started: list[int] = []

    async def handler(ctx):
        started.append(ctx.payload["v"])
        if ctx.payload["v"] == 1:
            await _enqueue(sm, clock, payload={"v": 2}, dedupe_key="silence:r1")

    await _enqueue(sm, clock, payload={"v": 1}, dedupe_key="silence:r1")
    await Worker(sm, clock, {"t": handler}).run_due()
    assert started == [1, 2]
    assert [j.status for j in await _jobs(sm)] == [JobStatus.DONE, JobStatus.DONE]


async def test_retry_of_superseded_job_is_cancelled(sm, clock):
    """The job fails while a newer one with the same key is already queued: the retry
    would break the unique index, so the old one is cancelled."""

    async def handler(ctx):
        if ctx.payload["v"] == 1:
            await _enqueue(
                sm,
                clock,
                payload={"v": 2},
                dedupe_key="d",
                run_at=clock.now() + timedelta(minutes=1),
            )
            raise RuntimeError("transient")

    old = await _enqueue(sm, clock, payload={"v": 1}, dedupe_key="d")
    await Worker(sm, clock, {"t": handler}).run_due()
    job = await _job(sm, old)
    assert job.status == JobStatus.CANCELLED
    assert (job.last_error or "").startswith("superseded")


async def test_cancel_and_cancel_prefix(sm, clock):
    await _enqueue(sm, clock, dedupe_key="silence:a")
    await _enqueue(sm, clock, dedupe_key="silence:b")
    await _enqueue(sm, clock, dedupe_key="expire:a")
    async with sm() as session, session.begin():
        assert await cancel(session, "expire:a") == 1
        assert await cancel(session, "expire:a") == 0
        assert await cancel_prefix(session, "silence:") == 2
    statuses = sorted(j.status for j in await _jobs(sm))
    assert statuses == [JobStatus.CANCELLED] * 3


async def test_two_workers_never_take_the_same_job(sm, clock):
    for i in range(10):
        await _enqueue(sm, clock, payload={"i": i})
    a = Worker(sm, clock, {}, worker_id="a")
    b = Worker(sm, clock, {}, worker_id="b")
    got_a, got_b = await asyncio.gather(a.claim(clock.now(), 10), b.claim(clock.now(), 10))
    ids_a = {j["id"] for j in got_a}
    ids_b = {j["id"] for j in got_b}
    assert not ids_a & ids_b
    assert len(ids_a | ids_b) == 10


async def test_expired_running_job_is_reclaimed(sm, clock):
    job_id = await _enqueue(sm, clock)
    dead = Worker(sm, clock, {}, worker_id="dead", lock_seconds=300)
    claimed = await dead.claim(clock.now(), 1)
    assert [j["id"] for j in claimed] == [job_id]

    alive = Worker(sm, clock, {}, worker_id="alive")
    assert await alive.claim(clock.now(), 1) == []
    clock.advance(seconds=301)
    again = await alive.claim(clock.now(), 1)
    assert [j["id"] for j in again] == [job_id]
    assert again[0]["attempts"] == 2

    # the dead worker can no longer finish it
    await dead._finish(JobContext(job_id, "t", {}, 1, 5, None, clock.now()), "done", None)
    assert (await _job(sm, job_id)).status == JobStatus.RUNNING


async def test_run_due_runs_jobs_queued_by_jobs(sm, clock):
    order: list[str] = []

    async def first(ctx):
        order.append("first")
        await _enqueue(sm, clock, kind="second")

    async def second(ctx):
        order.append("second")

    await _enqueue(sm, clock, kind="first")
    total = await Worker(sm, clock, {"first": first, "second": second}).run_due()
    assert total == 2
    assert order == ["first", "second"]
