"""Receipt checks by code (docs/03 §8): amount, currency, date, recipient, duplicates,
and who decides — code or a person."""

from datetime import date
from decimal import Decimal

from app.receipts.checks import (
    Expected,
    check_receipt,
    norm,
    reading_from,
    recipient_matches,
    review_reason,
    summary,
)

TODAY = date(2026, 10, 7)
EXPECTED = Expected(
    amount=Decimal("1500"),
    currency="UAH",
    recipients=("ФОП Іваненко Олена",),
    max_age_days=3,
    tolerance=Decimal("1"),
)


def _reading(**over):
    data = {
        "is_receipt": True,
        "readable": True,
        "amount": 1500,
        "currency": "uah",
        "date": "2026-10-06T14:03:00",
        "recipient": "Іваненко О.",
        "recipient_account": "UA213223130000026007233566001",
        "reference": "P24-123",
        "purpose": "Замовлення",
        "status": "success",
    }
    data.update(over)
    return reading_from(data)


def _codes(reading, expected=EXPECTED, **kw):
    return [i["code"] for i in check_receipt(reading, expected, today=TODAY, **kw)]


def test_a_good_receipt_has_no_issues():
    assert _codes(_reading()) == []


def test_reading_normalizes_values():
    r = _reading(amount="1 500,50", currency=" uah ")
    assert r.amount == Decimal("1500.50")
    assert r.currency == "UAH"
    assert r.paid_on == date(2026, 10, 6)
    assert reading_from({}).is_receipt is False
    assert reading_from({"is_receipt": True, "amount": "abc"}).amount is None


def test_not_a_receipt_and_unreadable_stop_the_checks():
    assert _codes(_reading(is_receipt=False)) == ["not_receipt"]
    assert _codes(_reading(readable=False)) == ["unreadable"]


def test_amount_within_the_tolerance_passes():
    assert _codes(_reading(amount="1499.00")) == []
    found = check_receipt(_reading(amount=1400), EXPECTED, today=TODAY)
    assert found == [
        {
            "code": "amount_mismatch",
            "params": {"expected": "1500", "got": "1400", "currency": "UAH"},
        }
    ]
    assert _codes(_reading(amount=None)) == ["amount_missing"]


def test_no_expected_amount_skips_the_amount_check():
    assert _codes(_reading(amount=None), Expected(currency="UAH")) == []


def test_currency_date_and_status():
    assert _codes(_reading(currency="USD")) == ["currency_mismatch"]
    assert _codes(_reading(date=None)) == ["date_missing"]
    assert _codes(_reading(date="2026-10-01")) == ["date_too_old"]
    assert _codes(_reading(date="2026-10-08")) == []  # a day for time zones
    assert _codes(_reading(date="2026-10-10")) == ["date_in_future"]
    assert _codes(_reading(status="pending")) == ["status_not_success"]


def test_duplicates_are_issues():
    codes = _codes(_reading(), duplicate_image=True, duplicate_reference=True)
    assert codes == ["duplicate_image", "duplicate_reference"]


def test_recipient_by_name_words_and_by_card_ending():
    r = _reading(recipient="ФОП ІВАНЕНКО ОЛЕНА ПЕТРІВНА", recipient_account=None)
    assert recipient_matches("ФОП Іваненко Олена", r)
    assert recipient_matches("Іваненко", _reading(recipient="Іваненко О.", recipient_account=None))
    assert not recipient_matches("ФОП Петренко", _reading(recipient_account=None))
    card = _reading(recipient=None, recipient_account="**** **** **** 4321")
    assert recipient_matches("5375 4141 0000 4321", card)
    assert not recipient_matches("5375 4141 0000 1111", card)
    iban = _reading(recipient=None)
    assert recipient_matches("UA213223130000026007233566001", iban)


def test_recipient_missing_and_mismatch():
    assert _codes(_reading(recipient=None, recipient_account=None)) == ["recipient_missing"]
    assert _codes(_reading(recipient="ТОВ Ромашка", recipient_account=None)) == [
        "recipient_mismatch"
    ]


def test_review_reason_by_the_tenant_mode():
    good = _reading()
    issues = check_receipt(_reading(amount=1), EXPECTED, today=TODAY)
    assert review_reason(issues, good, mode="issues_only", above={}) == "issues"
    assert review_reason([], good, mode="always", above={}) == "always"
    assert review_reason([], good, mode="issues_only", above={}) is None
    above = {"UAH": Decimal("1000")}
    assert review_reason([], good, mode="issues_only", above=above) == "amount_above"
    assert review_reason([], good, mode="issues_only", above={"USD": Decimal("1")}) is None


def test_summary_and_norm():
    r = _reading()
    assert summary(r) == "1500 UAH, 2026-10-06, Іваненко О., #P24-123"
    assert norm(r, "rid") == {
        "receipt_id": "rid",
        "amount": "1500",
        "currency": "UAH",
        "date": "2026-10-06",
        "recipient": "Іваненко О.",
        "reference": "P24-123",
    }
    assert summary(reading_from({"is_receipt": True})) == "receipt"


def test_expected_to_dict_is_json_ready():
    assert EXPECTED.to_dict() == {
        "amount": "1500",
        "currency": "UAH",
        "recipients": ["ФОП Іваненко Олена"],
        "max_age_days": 3,
        "tolerance": "1",
    }
