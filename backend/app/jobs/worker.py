"""Jobs worker: claims due jobs with SELECT … FOR UPDATE SKIP LOCKED, runs handlers,
retries with backoff. Woken by LISTEN/NOTIFY, falls back to polling.

`run_due(now)` runs everything that is due at `now` — tests use it with ManualClock
instead of waiting for real time.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.jobs.queue import NOTIFY_CHANNEL, next_run_at
from app.logging import get_logger

log = get_logger(__name__)

DEFAULT_BACKOFF_S = (10, 60, 300, 1800, 7200)


class Retry(Exception):
    """Handler asks to run again later (counts as an attempt)."""

    def __init__(self, delay_s: float | None = None, reason: str = "") -> None:
        super().__init__(reason)
        self.delay_s = delay_s
        self.reason = reason


class Fail(Exception):
    """Permanent failure: no more attempts."""


@dataclass
class JobContext:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    tenant_id: uuid.UUID | None
    now: datetime
    services: Any = field(repr=False, default=None)

    @property
    def is_last_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


Handler = Callable[[JobContext], Awaitable[None]]

_CLAIM_SQL = text(
    """
    WITH due AS (
        SELECT id FROM jobs
        WHERE (status = 'queued' AND run_at <= :now)
           OR (status = 'running' AND locked_until < :now)
        ORDER BY run_at
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    UPDATE jobs
    SET status = 'running', locked_by = :worker, locked_until = :lock_until,
        attempts = jobs.attempts + 1, updated_at = now()
    FROM due WHERE jobs.id = due.id
    RETURNING jobs.id, jobs.kind, jobs.payload, jobs.attempts, jobs.max_attempts,
              jobs.tenant_id, jobs.dedupe_key
    """
)


class Worker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Clock,
        handlers: dict[str, Handler],
        *,
        services: Any = None,
        worker_id: str | None = None,
        concurrency: int = 8,
        lock_seconds: int = 300,
        poll_seconds: float = 5.0,
        backoff_s: tuple[float, ...] = DEFAULT_BACKOFF_S,
    ) -> None:
        self.sm = sessionmaker
        self.clock = clock
        self.handlers = handlers
        self.services = services
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.concurrency = concurrency
        self.lock_seconds = lock_seconds
        self.poll_seconds = poll_seconds
        self.backoff_s = backoff_s
        self._wake = asyncio.Event()

    # --- claiming and running -------------------------------------------------------

    async def claim(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        async with self.sm() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        _CLAIM_SQL,
                        {
                            "now": now,
                            "limit": limit,
                            "worker": self.worker_id,
                            "lock_until": now + timedelta(seconds=self.lock_seconds),
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def run_job(self, job: dict[str, Any]) -> None:
        ctx = JobContext(
            id=job["id"],
            kind=job["kind"],
            payload=job["payload"] or {},
            attempts=job["attempts"],
            max_attempts=job["max_attempts"],
            tenant_id=job["tenant_id"],
            now=self.clock.now(),
            services=self.services,
        )
        handler = self.handlers.get(ctx.kind)
        bound = log.bind(job_id=str(ctx.id), kind=ctx.kind, attempt=ctx.attempts)
        if handler is None:
            bound.error("job.no_handler")
            await self._finish(ctx, "failed", f"no handler for kind {ctx.kind!r}")
            return
        try:
            await handler(ctx)
        except Retry as exc:
            await self._retry(ctx, job.get("dedupe_key"), exc.delay_s, exc.reason or "retry")
        except Fail as exc:
            bound.warning("job.failed", error=str(exc))
            await self._finish(ctx, "failed", str(exc))
        except Exception as exc:  # handler bug or transient error
            bound.exception("job.error")
            await self._retry(ctx, job.get("dedupe_key"), None, f"{type(exc).__name__}: {exc}")
        else:
            await self._finish(ctx, "done", None)

    async def _finish(self, ctx: JobContext, status: str, error: str | None) -> None:
        async with self.sm() as session, session.begin():
            await session.execute(
                text(
                    """
                    UPDATE jobs SET status = :status, last_error = :error,
                        finished_at = :now, locked_until = NULL, updated_at = now()
                    WHERE id = :id AND locked_by = :worker AND status = 'running'
                    """
                ),
                {
                    "status": status,
                    "error": error,
                    "now": self.clock.now(),
                    "id": ctx.id,
                    "worker": self.worker_id,
                },
            )

    async def _retry(
        self, ctx: JobContext, dedupe_key: str | None, delay_s: float | None, error: str
    ) -> None:
        if ctx.is_last_attempt:
            log.warning("job.gave_up", job_id=str(ctx.id), kind=ctx.kind, error=error)
            await self._finish(ctx, "failed", error)
            return
        if delay_s is None:
            delay_s = self.backoff_s[min(ctx.attempts - 1, len(self.backoff_s) - 1)]
        run_at = self.clock.now() + timedelta(seconds=delay_s)
        try:
            async with self.sm() as session, session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE jobs SET status = 'queued', run_at = :run_at, last_error = :error,
                            locked_by = NULL, locked_until = NULL, updated_at = now()
                        WHERE id = :id AND locked_by = :worker AND status = 'running'
                        """
                    ),
                    {"run_at": run_at, "error": error, "id": ctx.id, "worker": self.worker_id},
                )
        except IntegrityError:
            # a newer job with the same dedupe_key is already queued: it replaces this one
            log.info("job.superseded", job_id=str(ctx.id), dedupe_key=dedupe_key)
            await self._finish(ctx, "cancelled", f"superseded: {error}")

    async def run_due(self, now: datetime | None = None, *, max_rounds: int = 100) -> int:
        """Run every job due at `now` (and jobs those jobs queue for `now`). Sequential:
        deterministic for tests."""
        total = 0
        for _ in range(max_rounds):
            jobs = await self.claim(now or self.clock.now(), limit=self.concurrency)
            if not jobs:
                break
            for job in jobs:
                await self.run_job(job)
            total += len(jobs)
        return total

    # --- long-running loop ------------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event, dsn: str | None = None) -> None:
        listener = await self._listen(dsn) if dsn else None
        running: set[asyncio.Task[None]] = set()
        log.info("worker.started", worker_id=self.worker_id)
        try:
            while not stop.is_set():
                free = self.concurrency - len(running)
                jobs = await self.claim(self.clock.now(), limit=free) if free > 0 else []
                for job in jobs:
                    task = asyncio.create_task(self.run_job(job))
                    running.add(task)
                    task.add_done_callback(lambda t: (running.discard(t), self._wake.set()))
                if jobs and len(jobs) == free:
                    continue
                await self._sleep(stop)
        finally:
            if running:
                await asyncio.gather(*running, return_exceptions=True)
            if listener is not None:
                with contextlib.suppress(Exception):
                    await listener.close()
            log.info("worker.stopped", worker_id=self.worker_id)

    async def _sleep(self, stop: asyncio.Event) -> None:
        timeout = self.poll_seconds
        async with self.sm() as session:
            nxt = await next_run_at(session)
        if nxt is not None:
            timeout = max(0.05, min(timeout, (nxt - self.clock.now()).total_seconds()))
        self._wake.clear()
        stop_wait = asyncio.create_task(stop.wait())
        wake_wait = asyncio.create_task(self._wake.wait())
        try:
            await asyncio.wait(
                {stop_wait, wake_wait}, timeout=timeout, return_when="FIRST_COMPLETED"
            )
        finally:
            stop_wait.cancel()
            wake_wait.cancel()

    async def _listen(self, dsn: str) -> asyncpg.Connection:
        conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
        await conn.add_listener(NOTIFY_CHANNEL, lambda *_: self._wake.set())
        return conn
