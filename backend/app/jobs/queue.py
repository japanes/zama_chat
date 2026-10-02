"""enqueue / reschedule / cancel jobs. Call inside the caller's transaction: the job
becomes visible together with the data it belongs to (commit), and NOTIFY wakes
the worker only after that commit."""

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.db.models import Job, JobStatus

NOTIFY_CHANNEL = "zama_jobs"

OnConflict = Literal["reschedule", "keep", "earliest"]


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime,
    tenant_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
    on_conflict: OnConflict = "reschedule",
) -> uuid.UUID:
    """Queue a job. With dedupe_key, a still-queued job with the same key is updated
    instead of creating a second one:

    - reschedule: new run_at and payload (merge timer, silence timer);
    - keep: leave the existing job as is;
    - earliest: keep the earlier run_at, take the new payload.
    """
    values = {
        "id": new_id(),
        "tenant_id": tenant_id,
        "kind": kind,
        "payload": payload or {},
        "run_at": run_at,
        "status": JobStatus.QUEUED,
        "attempts": 0,
        "max_attempts": max_attempts,
        "dedupe_key": dedupe_key,
    }
    stmt = insert(Job).values(**values)
    if dedupe_key is not None:
        where = text("status = 'queued' AND dedupe_key IS NOT NULL")
        if on_conflict == "keep":
            stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"], index_where=where)
        else:
            new_run_at = (
                func.least(Job.run_at, stmt.excluded.run_at)
                if on_conflict == "earliest"
                else stmt.excluded.run_at
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["dedupe_key"],
                index_where=where,
                set_={
                    "run_at": new_run_at,
                    "payload": stmt.excluded.payload,
                    "updated_at": func.now(),
                },
            )
    stmt = stmt.returning(Job.id)
    job_id = (await session.execute(stmt)).scalar_one_or_none()
    if job_id is None:  # "keep" and it already exists
        job_id = (
            await session.execute(
                select(Job.id).where(Job.dedupe_key == dedupe_key, Job.status == JobStatus.QUEUED)
            )
        ).scalar_one()
    await session.execute(
        text("SELECT pg_notify(:ch, :kind)"), {"ch": NOTIFY_CHANNEL, "kind": kind}
    )
    return job_id


async def cancel(session: AsyncSession, dedupe_key: str) -> int:
    """Cancel the queued job with this key (a running one finishes normally)."""
    result = await session.execute(
        update(Job)
        .where(Job.dedupe_key == dedupe_key, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.CANCELLED, updated_at=func.now())
    )
    return result.rowcount or 0


async def cancel_prefix(session: AsyncSession, prefix: str) -> int:
    result = await session.execute(
        update(Job)
        .where(Job.dedupe_key.startswith(prefix, autoescape=True), Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.CANCELLED, updated_at=func.now())
    )
    return result.rowcount or 0


async def next_run_at(session: AsyncSession) -> datetime | None:
    return (
        await session.execute(select(func.min(Job.run_at)).where(Job.status == JobStatus.QUEUED))
    ).scalar_one_or_none()
