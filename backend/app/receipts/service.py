"""A receipt in a turn (docs/03 §8).

Think part (outside any transaction, like the rest of the turn): download the image,
the vision model reads it, code checks it against the order and earlier receipts and
decides — accepted / to a person / asked again. Write part (the turn's write
transaction): the `receipts` row, a review_receipt task and the notification.

An operator's decision on a receipt in review (operator API) goes through `decide()`.
"""

import base64
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_settings import TenantSettings
from app.db.base import new_id
from app.db.models import (
    CatalogItem,
    Conversation,
    LlmRole,
    OperatorTask,
    Receipt,
    ReceiptStatus,
    TaskKind,
    TaskStatus,
)
from app.errors import AppError
from app.logging import get_logger
from app.prompts.builtin import RECEIPT_VISION, RECEIPT_VISION_KEY
from app.prompts.render import render_json, to_text
from app.prompts.store import PromptText
from app.receipts.checks import (
    Expected,
    Reading,
    check_receipt,
    issue,
    norm,
    reading_from,
    review_reason,
    summary,
)
from app.receipts.fetch import FetchError, fetch
from app.scenarios.fields import FieldState
from app.scenarios.schema import FieldDef
from app.scenarios.typed import local_now

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

DEFAULT_PROMPT = PromptText(RECEIPT_VISION_KEY, 0, to_text(RECEIPT_VISION))
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_receipt": {"type": "boolean"},
        "readable": {"type": "boolean"},
        "amount": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"]},
        "recipient": {"type": ["string", "null"]},
        "recipient_account": {"type": ["string", "null"]},
        "reference": {"type": ["string", "null"]},
        "purpose": {"type": ["string", "null"]},
        "status": {"type": ["string", "null"]},
    },
    "required": [
        "is_receipt",
        "readable",
        "amount",
        "currency",
        "date",
        "recipient",
        "recipient_account",
        "reference",
        "purpose",
        "status",
    ],
    "additionalProperties": False,
}

# the client is asked to send it again (typed.ISSUE_TEXTS), no person needed
RETRY_ISSUES = {
    "not_receipt": "receipt_not_receipt",
    "unreadable": "receipt_unreadable",
    "fetch_failed": "receipt_unreadable",
    "not_image": "receipt_unreadable",
}


@dataclass
class ReceiptCheck:
    """One receipt image checked in a turn."""

    run_id: uuid.UUID
    field_key: str
    attachment: dict[str, Any]
    expected: Expected
    id: uuid.UUID = field(default_factory=new_id)
    sha256: str | None = None
    content_type: str | None = None
    reading: Reading | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)
    # why a person checks it: always / issues / amount_above; None = decided by code
    review: str | None = None
    llm_call_id: str | None = None

    @property
    def retry_issue(self) -> str | None:
        """The field issue when the client must send another image."""
        for found in self.issues:
            if found["code"] in RETRY_ISSUES:
                return RETRY_ISSUES[found["code"]]
        return None

    @property
    def status(self) -> str:
        if self.retry_issue is not None:
            return ReceiptStatus.REJECTED
        return ReceiptStatus.REVIEW if self.review else ReceiptStatus.ACCEPTED

    @property
    def value(self) -> str:
        return summary(self.reading) if self.reading else "receipt"

    @property
    def norm(self) -> dict[str, Any]:
        if self.reading is None:
            return {"receipt_id": str(self.id)}
        return norm(self.reading, str(self.id))

    def trace(self) -> dict[str, Any]:
        return {
            "receipt_id": str(self.id),
            "field": self.field_key,
            "status": self.status,
            "review": self.review,
            "issues": [i["code"] for i in self.issues],
        }


async def expected_for(
    session: AsyncSession, tenant_id: uuid.UUID, f: FieldDef, states: dict[str, FieldState]
) -> Expected:
    """What the receipt must show: a fixed amount, or the price of the item chosen in
    `amount_field` × the number in `quantity_field`."""
    rs = f.receipt
    if rs is None:
        return Expected()
    amount = rs.amount
    currency = rs.currency
    if rs.amount_field:
        ref = states[rs.amount_field].ref if rs.amount_field in states else None
        item = None
        if ref:
            item = (
                await session.execute(
                    select(CatalogItem).where(
                        CatalogItem.tenant_id == tenant_id, CatalogItem.external_id == ref
                    )
                )
            ).scalar_one_or_none()
        if item is not None and item.price is not None:
            amount = Decimal(item.price)
            currency = currency or (item.currency or "").upper() or None
    if amount is not None and rs.quantity_field and rs.quantity_field in states:
        quantity = states[rs.quantity_field].norm
        if isinstance(quantity, int | float) and not isinstance(quantity, bool) and quantity > 0:
            amount = amount * Decimal(str(quantity))
    return Expected(
        amount=amount,
        currency=currency,
        recipients=tuple(rs.recipients),
        max_age_days=rs.max_age_days,
        tolerance=rs.tolerance,
    )


async def _duplicates(
    session: AsyncSession, tenant_id: uuid.UUID, sha256: str | None, reference: str | None
) -> tuple[bool, bool]:
    """The same image / transaction in another receipt that was not rejected."""
    used = Receipt.status.in_((ReceiptStatus.ACCEPTED, ReceiptStatus.REVIEW))
    image = False
    if sha256:
        image = (
            await session.execute(
                select(Receipt.id)
                .where(Receipt.tenant_id == tenant_id, Receipt.sha256 == sha256, used)
                .limit(1)
            )
        ).first() is not None
    same_ref = False
    if reference:
        same_ref = (
            await session.execute(
                select(Receipt.id)
                .where(Receipt.tenant_id == tenant_id, Receipt.reference == reference, used)
                .limit(1)
            )
        ).first() is not None
    return image, same_ref


def vision_messages(
    prompt: PromptText, content_type: str, data: bytes, today: str
) -> list[dict[str, Any]]:
    body = render_json(prompt.body, {"today": today}, key=prompt.key)
    image = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": to_text(body)},
                {"type": "image_url", "image_url": {"url": image}},
            ],
        }
    ]


async def check_attachment(
    services: "Services",
    *,
    tenant_id: uuid.UUID,
    turn_id: uuid.UUID | None,
    settings: TenantSettings,
    prompt: PromptText | None,
    check: ReceiptCheck,
    now: datetime,
) -> ReceiptCheck:
    """Fill `check` (sha256, reading, issues, review). Never raises: whatever fails ends
    in an issue — the client is asked again or a person looks at it."""
    try:
        fetched = await fetch(
            services.http,
            str(check.attachment.get("url") or ""),
            max_bytes=int(services.settings.receipt_max_mb * 1024 * 1024),
            allow_private=services.settings.receipt_fetch_private,
        )
    except FetchError as exc:
        log.info("receipt.fetch_failed", code=exc.code, receipt_id=str(check.id))
        check.issues = [issue("fetch_failed", reason=exc.code)]
        return check
    check.sha256 = fetched.sha256
    check.content_type = fetched.content_type
    if fetched.content_type not in IMAGE_TYPES:
        check.issues = [issue("not_image", content_type=fetched.content_type)]
        return check

    today = local_now(now, settings.timezone).date()
    prompt = prompt or DEFAULT_PROMPT
    try:
        result = await services.llm.complete(
            LlmRole.VISION,
            vision_messages(prompt, fetched.content_type, fetched.data, today.isoformat()),
            tenant_id=tenant_id,
            turn_id=turn_id,
            schema=VISION_SCHEMA,
            schema_name="receipt",
            prompt_key=prompt.key,
            prompt_version=prompt.version or None,
        )
    except AppError as exc:
        # no vision model / the model failed: a person reads the receipt
        log.warning("receipt.vision_failed", code=exc.code, receipt_id=str(check.id))
        check.issues = [issue("vision_failed", reason=exc.code)]
        check.review = "issues"
        return check
    check.llm_call_id = str(result.call_id) if result.call_id else None
    reading = reading_from(result.data)
    check.reading = reading

    async with services.sm() as session:
        dup_image, dup_ref = await _duplicates(
            session, tenant_id, fetched.sha256, reading.reference
        )
    check.issues = check_receipt(
        reading,
        check.expected,
        today=today,
        duplicate_image=dup_image,
        duplicate_reference=dup_ref,
    )
    if check.retry_issue is None:
        check.review = review_reason(
            check.issues,
            reading,
            mode=settings.receipt_review,
            above=settings.receipt_review_above,
        )
    return check


# --- write ----------------------------------------------------------------------------------


def receipt_row(
    check: ReceiptCheck, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, now: datetime
) -> Receipt:
    message_id = check.attachment.get("message_id")
    return Receipt(
        id=check.id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        run_id=check.run_id,
        field_key=check.field_key,
        message_id=uuid.UUID(message_id) if message_id else None,
        url=str(check.attachment.get("url") or ""),
        content_type=check.content_type,
        sha256=check.sha256,
        extracted=dict(check.reading.raw) if check.reading else {},
        reference=check.reading.reference if check.reading else None,
        expected=check.expected.to_dict(),
        issues=list(check.issues),
        status=check.status,
        review_reason=check.review,
        decided_at=None if check.status == ReceiptStatus.REVIEW else now,
        llm_call_id=uuid.UUID(check.llm_call_id) if check.llm_call_id else None,
    )


def review_payload(
    check: ReceiptCheck, *, scenario: str, scenario_title: str, fields: str
) -> dict[str, Any]:
    return {
        "receipt_id": str(check.id),
        "run_id": str(check.run_id),
        "field": check.field_key,
        "scenario": scenario,
        "scenario_title": scenario_title,
        "url": check.attachment.get("url"),
        "extracted": dict(check.reading.raw) if check.reading else {},
        "expected": check.expected.to_dict(),
        "issues": list(check.issues),
        "review_reason": check.review,
        "fields": fields,
    }


async def open_review(
    session: AsyncSession,
    conv: Conversation,
    *,
    turn_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> OperatorTask:
    task = OperatorTask(
        tenant_id=conv.tenant_id,
        channel_id=conv.channel_id,
        conversation_id=conv.id,
        turn_id=turn_id,
        kind=TaskKind.REVIEW_RECEIPT,
        status=TaskStatus.OPEN,
        version=1,
        payload=payload,
        result={},
    )
    session.add(task)
    await session.flush()
    return task


def issues_text(issues: list[dict[str, Any]]) -> str:
    """For notifications (plain codes; the admin shows them translated)."""
    if not issues:
        return "-"
    return ", ".join(
        i["code"] + (f" {json.dumps(i['params'], ensure_ascii=False)}" if i.get("params") else "")
        for i in issues
    )


# --- a person's decision ---------------------------------------------------------------------


async def decide(
    session: AsyncSession,
    services: "Services",
    conv: Conversation,
    task: OperatorTask,
    *,
    accept: bool,
    user_id: uuid.UUID,
    note: str | None,
    now: datetime,
) -> dict[str, Any]:
    """A receipt in review accepted / rejected (the conversation is under the caller's
    lease). The run waiting for it goes on: completed / waiting for an event, or back to
    the client for a correct receipt; the client gets the bot's message."""
    from app.scenarios import fields as fl
    from app.scenarios.outside import (
        Change,
        apply_receipt_decision,
        language_of,
        lock_run,
        settle,
        write_run,
    )
    from app.scenarios.store import load_definition

    receipt_id = uuid.UUID(str((task.payload or {}).get("receipt_id")))
    receipt = await session.get(Receipt, receipt_id, with_for_update=True)
    if receipt is None or receipt.tenant_id != conv.tenant_id:
        raise AppError("receipt.not_found", {"id": str(receipt_id)}, status=404)
    if receipt.status != ReceiptStatus.REVIEW:
        raise AppError("receipt.decided", {"status": receipt.status}, status=409)
    receipt.status = ReceiptStatus.ACCEPTED if accept else ReceiptStatus.REJECTED
    receipt.decided_by = user_id
    receipt.decided_at = now
    receipt.decision_note = note
    out: dict[str, Any] = {"receipt_id": str(receipt.id), "receipt_status": receipt.status}

    run = await lock_run(session, receipt.run_id)
    data = dict(run.data or {}) if run is not None else {}
    if run is None or data.get("receipt_id") != str(receipt.id):
        # the run moved on (expired, cancelled): only the receipt is decided
        out["run"] = None
        return out
    defn = await load_definition(session, run.scenario_id)
    states = fl.load_states(defn, run.fields)
    change = Change()
    apply_receipt_decision(
        defn,
        states,
        data,
        field_key=receipt.field_key,
        accept=accept,
        language=language_of(conv, run, defn),
        change=change,
        now=now,
    )
    await settle(session, run, defn, states, data, change, now)
    await write_run(
        session,
        services,
        conv,
        run,
        defn,
        states,
        data,
        change,
        now,
        meta={"receipt_id": str(receipt.id), "task_id": str(task.id)},
    )
    out["run"] = {"id": str(run.id), "status": run.status, "asking": run.asking}
    log.info("receipt.decided", receipt_id=str(receipt.id), status=receipt.status)
    return out
