"""Sending results to external APIs (docs/04 §5 «Отправка результата»).

- queue_delivery (inside the turn / job transaction): a new version of the result,
  `seq` + 1; older versions to the same endpoint that are still waiting are cancelled —
  otherwise a retried partial JSON could arrive after the full one and overwrite it;
- deliver_job: POST with `Idempotency-Key: <delivery_id>` and
  `X-Zama-Signature: sha256=HMAC(secret, body)`. 2xx → sent; 4xx (except 408 / 429) →
  failed at once; 5xx and network errors → retries after 1 min, 5 min, 30 min, 2 h, 6 h,
  then failed (seen in the admin, "send again" there).
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.db.models import (
    Channel,
    Contact,
    Conversation,
    Delivery,
    DeliveryStatus,
    Endpoint,
    ScenarioRun,
)
from app.jobs.queue import cancel, enqueue
from app.jobs.worker import Fail, JobContext, Retry
from app.logging import get_logger
from app.results.service import build_payload
from app.scenarios.schema import ScenarioDef
from app.scenarios.store import load_definition

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

RETRY_DELAYS_S = (60, 300, 1800, 7200, 21600)
MAX_ATTEMPTS = len(RETRY_DELAYS_S) + 1
TIMEOUT_S = 15.0
NO_RETRY_4XX_EXCEPT = (408, 429)
SIGNATURE_HEADER = "X-Zama-Signature"
EVENT_HEADER = "X-Zama-Event"
IDEMPOTENCY_HEADER = "Idempotency-Key"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def encode_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


async def find_endpoint(session: AsyncSession, tenant_id: uuid.UUID, name: str) -> Endpoint | None:
    return (
        await session.execute(
            select(Endpoint).where(
                Endpoint.tenant_id == tenant_id, Endpoint.name == name, Endpoint.is_active
            )
        )
    ).scalar_one_or_none()


async def _client(session: AsyncSession, run: ScenarioRun) -> tuple[Contact | None, Channel | None]:
    conv = await session.get(Conversation, run.conversation_id)
    contact = await session.get(Contact, conv.contact_id) if conv is not None else None
    channel = await session.get(Channel, run.channel_id)
    return contact, channel


async def queue_delivery(
    session: AsyncSession,
    run: ScenarioRun,
    defn: ScenarioDef,
    *,
    endpoint_name: str,
    event: str,
    status: str,
    now: datetime,
) -> Delivery | None:
    """`run` must be locked by the caller (seq is counted on it)."""
    endpoint = await find_endpoint(session, run.tenant_id, endpoint_name)
    if endpoint is None:
        # the scenario refers to an endpoint the tenant has not set up: not the client's
        # problem, the turn goes on
        log.warning(
            "delivery.endpoint_missing",
            run_id=str(run.id),
            endpoint=endpoint_name,
            result_event=event,
        )
        return None
    run.delivery_seq = (run.delivery_seq or 0) + 1
    contact, channel = await _client(session, run)
    delivery_id = new_id()
    payload = build_payload(
        run,
        defn,
        contact=contact,
        channel=channel,
        event=event,
        status=status,
        delivery_id=str(delivery_id),
        seq=run.delivery_seq,
    )

    older = list(
        (
            await session.execute(
                update(Delivery)
                .where(
                    Delivery.run_id == run.id,
                    Delivery.endpoint_id == endpoint.id,
                    Delivery.status == DeliveryStatus.PENDING,
                )
                .values(status=DeliveryStatus.CANCELLED, last_error="replaced by a newer version")
                .returning(Delivery.id)
            )
        ).scalars()
    )
    for old_id in older:
        await cancel(session, f"deliver:{old_id}")

    delivery = Delivery(
        id=delivery_id,
        tenant_id=run.tenant_id,
        run_id=run.id,
        endpoint_id=endpoint.id,
        event=event,
        seq=run.delivery_seq,
        payload=payload,
        status=DeliveryStatus.PENDING,
        attempts=0,
    )
    session.add(delivery)
    await session.flush()
    await enqueue(
        session,
        "deliver",
        {"delivery_id": str(delivery_id)},
        run_at=now,
        tenant_id=run.tenant_id,
        dedupe_key=f"deliver:{delivery_id}",
        max_attempts=MAX_ATTEMPTS,
    )
    log.info(
        "delivery.queued",
        delivery_id=str(delivery_id),
        run_id=str(run.id),
        seq=run.delivery_seq,
        result_event=event,
        cancelled=[str(i) for i in older],
    )
    return delivery


async def resend(
    session: AsyncSession, delivery_id: uuid.UUID, *, now: datetime
) -> Delivery | None:
    """Admin "send again": a new version (current fields, new seq) of the same event."""
    old = await session.get(Delivery, delivery_id)
    if old is None:
        return None
    run = await session.get(ScenarioRun, old.run_id, with_for_update=True)
    endpoint = await session.get(Endpoint, old.endpoint_id) if old.endpoint_id else None
    if run is None or endpoint is None:
        return None
    defn = await load_definition(session, run.scenario_id)
    return await queue_delivery(
        session,
        run,
        defn,
        endpoint_name=endpoint.name,
        event=old.event,
        status=str(old.payload.get("status") or ""),
        now=now,
    )


# --- the job ------------------------------------------------------------------------------


def retry_delay(attempt: int) -> float:
    return RETRY_DELAYS_S[min(max(attempt, 1) - 1, len(RETRY_DELAYS_S) - 1)]


def is_permanent(code: int) -> bool:
    return 400 <= code < 500 and code not in NO_RETRY_4XX_EXCEPT


async def _record(
    services: "Services",
    delivery_id: uuid.UUID,
    *,
    status: DeliveryStatus | None,
    code: int | None = None,
    error: str | None = None,
) -> None:
    async with services.sm() as session, session.begin():
        row = await session.get(Delivery, delivery_id, with_for_update=True)
        if row is None:
            return
        row.attempts = (row.attempts or 0) + 1
        row.last_code = code
        row.last_error = error[:2000] if error else None
        if status is not None:
            row.status = status
            if status == DeliveryStatus.SENT:
                row.sent_at = services.clock.now()


async def deliver_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    delivery_id = uuid.UUID(ctx.payload["delivery_id"])
    async with services.sm() as session:
        row = await session.get(Delivery, delivery_id)
        if row is None:
            raise Fail(f"delivery {delivery_id} not found")
        if row.status != DeliveryStatus.PENDING:
            return  # replaced by a newer version / resolved
        endpoint = await session.get(Endpoint, row.endpoint_id) if row.endpoint_id else None
        newer_sent = (
            await session.execute(
                select(
                    exists().where(
                        Delivery.run_id == row.run_id,
                        Delivery.endpoint_id == row.endpoint_id,
                        Delivery.seq > row.seq,
                        Delivery.status == DeliveryStatus.SENT,
                    )
                )
            )
        ).scalar_one()
        payload = row.payload
        event = row.event

    if newer_sent:
        await _record(services, delivery_id, status=DeliveryStatus.CANCELLED, error="newer sent")
        return
    if endpoint is None or not endpoint.is_active:
        await _record(services, delivery_id, status=DeliveryStatus.FAILED, error="no endpoint")
        return

    body = encode_body(payload)
    # TODO(review): endpoint headers may hold API tokens — encrypt them like the secret
    headers = {str(k): str(v) for k, v in (endpoint.headers or {}).items()}
    headers.update(
        {
            "Content-Type": "application/json; charset=utf-8",
            IDEMPOTENCY_HEADER: str(delivery_id),
            EVENT_HEADER: f"scenario.{event}",
        }
    )
    if endpoint.secret_enc:
        headers[SIGNATURE_HEADER] = sign(services.crypto.decrypt(endpoint.secret_enc), body)

    last = ctx.is_last_attempt
    try:
        response = await services.http.post(
            endpoint.url, content=body, headers=headers, timeout=TIMEOUT_S
        )
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"
        await _record(
            services, delivery_id, status=DeliveryStatus.FAILED if last else None, error=error
        )
        if last:
            log.warning("delivery.failed", delivery_id=str(delivery_id), error=error)
            return
        raise Retry(retry_delay(ctx.attempts), error) from exc

    code = response.status_code
    if 200 <= code < 300:
        await _record(services, delivery_id, status=DeliveryStatus.SENT, code=code)
        log.info("delivery.sent", delivery_id=str(delivery_id), code=code)
        return
    error = f"HTTP {code}: {response.text[:500]}"
    if is_permanent(code) or last:
        await _record(services, delivery_id, status=DeliveryStatus.FAILED, code=code, error=error)
        log.warning("delivery.failed", delivery_id=str(delivery_id), code=code)
        return
    await _record(services, delivery_id, status=None, code=code, error=error)
    raise Retry(retry_delay(ctx.attempts), error)
