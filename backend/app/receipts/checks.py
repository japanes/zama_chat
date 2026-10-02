"""What code checks on a receipt the vision model read (docs/03 §8). Pure functions.

Issues (code + params; the admin shows them in its language, `receipt_issue.<code>`):
  not_receipt / unreadable           — the client is asked again, no person needed;
  not_image / vision_failed          — a person looks at it;
  amount_missing / amount_mismatch   — the amount is not the expected one;
  currency_mismatch, date_missing, date_too_old, date_in_future,
  recipient_missing / recipient_mismatch, status_not_success,
  duplicate_image / duplicate_reference — this receipt (or its transaction) came before.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

CLIENT_RETRY = ("not_receipt", "unreadable")
# legal forms do not tell one receiver from another
LEGAL_FORMS = {"фоп", "тов", "пп", "ип", "ооо", "llc", "ltd", "inc", "fop", "tov"}


@dataclass(frozen=True)
class Expected:
    amount: Decimal | None = None
    currency: str | None = None
    recipients: tuple[str, ...] = ()
    max_age_days: int = 3
    tolerance: Decimal = Decimal(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "recipients": list(self.recipients),
            "max_age_days": self.max_age_days,
            "tolerance": str(self.tolerance),
        }


@dataclass(frozen=True)
class Reading:
    is_receipt: bool
    readable: bool = True
    amount: Decimal | None = None
    currency: str | None = None
    paid_on: date | None = None
    recipient: str | None = None
    recipient_account: str | None = None
    reference: str | None = None
    purpose: str | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        found = Decimal(text)
    except InvalidOperation:
        return None
    return found if found.is_finite() else None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def reading_from(data: dict[str, Any] | None) -> Reading:
    data = data or {}
    currency = _text(data.get("currency"))
    return Reading(
        is_receipt=data.get("is_receipt") is True,
        readable=data.get("readable") is not False,
        amount=_decimal(data.get("amount")),
        currency=currency.upper() if currency else None,
        paid_on=_date(data.get("date")),
        recipient=_text(data.get("recipient")),
        recipient_account=_text(data.get("recipient_account")),
        reference=_text(data.get("reference")),
        purpose=_text(data.get("purpose")),
        status=_text(data.get("status")),
        raw=dict(data),
    )


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", text.casefold()) if w not in LEGAL_FORMS]


def recipient_matches(expected: str, reading: Reading) -> bool:
    """A card ending / IBAN by its digits, a name by its main words.
    TODO(review): names on receipts are often shortened («Іваненко І.»): one long word of
    the expected name is enough; a mismatch only sends the receipt to a person."""
    expected = expected.strip()
    if not expected:
        return False
    got = " ".join(t for t in (reading.recipient, reading.recipient_account) if t)
    if not got:
        return False
    digits = re.sub(r"\D", "", expected)
    if digits and len(digits) >= 4 and len(digits) >= len(_norm(expected)) - 4:
        got_digits = re.sub(r"\D", "", got)
        return digits in got_digits or got_digits.endswith(digits[-4:])
    if _norm(expected) in _norm(got) or (_norm(got) and _norm(got) in _norm(expected)):
        return True
    got_words = _words(got)
    for word in _words(expected):
        if len(word) >= 4 and any(g.startswith(word[: max(4, len(word) - 2)]) for g in got_words):
            return True
    return False


def issue(code: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "params": {k: v for k, v in params.items() if v is not None}}


def check_receipt(
    reading: Reading,
    expected: Expected,
    *,
    today: date,
    duplicate_image: bool = False,
    duplicate_reference: bool = False,
) -> list[dict[str, Any]]:
    if not reading.is_receipt:
        return [issue("not_receipt")]
    if not reading.readable:
        return [issue("unreadable")]
    found: list[dict[str, Any]] = []
    if duplicate_image:
        found.append(issue("duplicate_image"))
    if duplicate_reference:
        found.append(issue("duplicate_reference", reference=reading.reference))
    if expected.amount is not None:
        if reading.amount is None:
            found.append(issue("amount_missing"))
        elif abs(reading.amount - expected.amount) > expected.tolerance:
            found.append(
                issue(
                    "amount_mismatch",
                    expected=str(expected.amount),
                    got=str(reading.amount),
                    currency=expected.currency,
                )
            )
    if expected.currency and reading.currency and reading.currency != expected.currency:
        found.append(issue("currency_mismatch", expected=expected.currency, got=reading.currency))
    if reading.paid_on is None:
        found.append(issue("date_missing"))
    elif reading.paid_on > today + timedelta(days=1):  # a day for time zones
        found.append(issue("date_in_future", date=reading.paid_on.isoformat()))
    elif reading.paid_on < today - timedelta(days=expected.max_age_days):
        found.append(
            issue("date_too_old", date=reading.paid_on.isoformat(), max_days=expected.max_age_days)
        )
    if expected.recipients:
        if not (reading.recipient or reading.recipient_account):
            found.append(issue("recipient_missing"))
        elif not any(recipient_matches(r, reading) for r in expected.recipients):
            found.append(
                issue(
                    "recipient_mismatch",
                    expected=", ".join(expected.recipients),
                    got=reading.recipient or reading.recipient_account,
                )
            )
    if reading.status and reading.status.lower() not in ("success", "successful", "done"):
        found.append(issue("status_not_success", status=reading.status))
    return found


def review_reason(
    issues: list[dict[str, Any]],
    reading: Reading,
    *,
    mode: str,
    above: dict[str, Decimal],
) -> str | None:
    """None → accepted by code. `mode` is the tenant setting receipt_review:
    always — every receipt goes to a person (the default, docs/03 §8);
    issues_only — only receipts with issues or with an amount above the threshold."""
    if issues:
        return "issues"
    if mode != "issues_only":
        return "always"
    limit = above.get(reading.currency or "")
    if limit is not None and reading.amount is not None and reading.amount > limit:
        return "amount_above"
    return None


def summary(reading: Reading) -> str:
    """The field value of an accepted receipt: «500.00 UAH, 2026-10-01, ФОП Іваненко»."""
    parts = []
    if reading.amount is not None:
        parts.append(f"{reading.amount} {reading.currency or ''}".strip())
    if reading.paid_on is not None:
        parts.append(reading.paid_on.isoformat())
    if reading.recipient:
        parts.append(reading.recipient)
    if reading.reference:
        parts.append(f"#{reading.reference}")
    return ", ".join(parts) or "receipt"


def norm(reading: Reading, receipt_id: str) -> dict[str, Any]:
    return {
        "receipt_id": receipt_id,
        "amount": str(reading.amount) if reading.amount is not None else None,
        "currency": reading.currency,
        "date": reading.paid_on.isoformat() if reading.paid_on else None,
        "recipient": reading.recipient,
        "reference": reading.reference,
    }
