"""Typed fields (stage 6): code normalizes dates, times, phones, e-mails, numbers and
lists; what code rejects is re-asked without Jev."""

from datetime import UTC, date, datetime

import pytest

from app.scenarios import typed
from app.scenarios.schema import parse_definition
from tests.scenario_helpers import booking_data

# Wednesday, 2026-10-07, 10:00 Kyiv time
NOW = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
TODAY = date(2026, 10, 7)


def _field(type_: str, **settings):
    data = booking_data(
        fields=[{"key": "x", "type": type_, "question": {"uk": "?", "en": "?"}, **settings}]
    )
    return parse_definition(data).field("x")


def _norm(type_: str, value, llm=None, **settings):
    return typed.normalize(
        _field(type_, **settings), value, llm, now=NOW, timezone="Europe/Kyiv", region="UA"
    )


# --- date -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("сьогодні", date(2026, 10, 7)),
        ("завтра", date(2026, 10, 8)),
        ("послезавтра", date(2026, 10, 9)),
        ("day after tomorrow", date(2026, 10, 9)),
        ("в п'ятницю", date(2026, 10, 9)),
        ("у понеділок", date(2026, 10, 12)),
        ("наступної п'ятниці", date(2026, 10, 16)),
        ("12.10", date(2026, 10, 12)),
        ("5.01", date(2027, 1, 5)),  # in October «5.01» is next January
        ("12.10.2026", date(2026, 10, 12)),
    ],
)
def test_own_date_rules(text, expected):
    assert typed.own_date(text, TODAY) == expected


def test_a_date_in_the_past_is_an_issue():
    result = _norm("date", "1.10.2026")
    assert result.norm == "2026-10-01"
    assert result.issue == "date_in_past"


def test_a_past_date_is_fine_when_allowed():
    result = _norm("date", "1.10.2026", date={"allow_past": True})
    assert (result.norm, result.issue) == ("2026-10-01", None)


def test_a_date_too_far_ahead_carries_the_limit():
    result = _norm("date", "20.12.2026", date={"max_days_ahead": 30})
    assert result.issue == "date_too_far"
    assert result.params == {"max": 30}


def test_the_model_date_is_used_when_code_finds_none():
    result = _norm("date", "на день народження мами", "2026-10-20")
    assert (result.norm, result.source) == ("2026-10-20", "llm")


def test_no_date_at_all_is_date_invalid():
    result = _norm("date", "коли зручно", None)
    assert result.issue == "date_invalid"
    assert result.norm is None


def test_today_is_taken_in_the_tenant_time_zone():
    late = datetime(2026, 10, 7, 22, 30, tzinfo=UTC)  # already the 8th in Kyiv
    result = typed.normalize(
        _field("date"), "сьогодні", None, now=late, timezone="Europe/Kyiv", region="UA"
    )
    assert result.norm == "2026-10-08"


def test_a_broken_time_zone_does_not_stop_the_turn():
    assert typed.local_now(NOW, "Mars/Olympus") == NOW


# --- time -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("о 15:30", "15:30"),
        ("10.00", "10:00"),
        ("в 5 вечора", "17:00"),
        ("3 pm", "15:00"),
        ("11am", "11:00"),
        ("на 9", "09:00"),
    ],
)
def test_own_time_rules(text, expected):
    assert typed.own_time(text) == expected


def test_a_range_is_left_to_the_model():
    assert typed.own_time("з 10 до 12") is None
    assert _norm("time", "з 10 до 12", "10:00").norm == "10:00"
    assert _norm("time", "з 10 до 12", "10 ранку").issue == "time_invalid"


# --- phone / e-mail ---------------------------------------------------------------------


def test_a_ukrainian_phone_becomes_e164():
    assert _norm("phone", "067 123 45 67").norm == "+380671234567"
    assert _norm("phone", "мій номер +38 (067) 123-45-67").norm == "+380671234567"


def test_a_short_phone_is_an_issue():
    assert _norm("phone", "12345").issue == "phone_invalid"


def test_two_phones_are_left_unclear():
    assert _norm("phone", "067 123 45 67 або 050 765 43 21").issue == "phone_invalid"


def test_email():
    assert _norm("email", "пишіть на Anna.K@example.com").norm == "anna.k@example.com"
    assert _norm("email", "anna at example").issue == "email_invalid"


def test_scan_contacts_finds_one_phone_and_one_email():
    found = typed.scan_contacts("Анна, 0671234567, anna@example.com", "UA")
    assert found[typed.FieldType.PHONE] == ["+380671234567"]
    assert found[typed.FieldType.EMAIL] == ["anna@example.com"]


# --- number -----------------------------------------------------------------------------


def test_numbers_and_limits():
    settings = {"number": {"min": 1, "max": 20, "integer": True}}
    assert _norm("number", "3 штуки", **settings).norm == 3
    assert _norm("number", "2,5", **settings).issue == "number_not_integer"
    assert _norm("number", "0", **settings).issue == "number_too_small"
    result = _norm("number", "25", **settings)
    assert (result.issue, result.params) == ("number_too_large", {"max": 20})


def test_a_number_in_words_comes_from_the_model():
    result = _norm("number", "три", 3)
    assert (result.norm, result.source) == (3, "llm")
    assert _norm("number", "кілька", None).issue == "number_invalid"


# --- list -------------------------------------------------------------------------------

GUESTS = {
    "items": {
        "min_items": 2,
        "max_items": 3,
        "item_fields": [
            {"key": "name", "label": {"uk": "Ім'я", "en": "Name"}},
            {"key": "service", "label": {"uk": "Послуга", "en": "Service"}},
        ],
    }
}


def test_a_list_of_objects_is_cleaned_and_rendered():
    raw = [
        {"name": " Оля ", "service": "манікюр", "extra": "x"},
        {"name": "Ira", "service": "brows"},
    ]
    result = _norm("list", raw, **GUESTS)
    assert result.issue is None
    assert result.norm == [
        {"name": "Оля", "service": "манікюр"},
        {"name": "Ira", "service": "brows"},
    ]
    assert result.value == "1. Оля — манікюр\n2. Ira — brows"


def test_list_limits_and_incomplete_items():
    assert _norm("list", [{"name": "Оля", "service": "манікюр"}], **GUESTS).issue == "list_too_few"
    many = [{"name": str(n), "service": "s"} for n in range(4)]
    assert _norm("list", many, **GUESTS).issue == "list_too_many"
    result = _norm("list", [{"name": "Оля", "service": "м"}, {"name": "Іра"}], **GUESTS)
    assert result.issue == "list_item_incomplete"
    assert result.params == {"n": 2, "fields": ["service"]}


def test_a_plain_list_keeps_strings():
    result = _norm("list", ["  хліб ", "", None, "молоко"])
    assert result.norm == ["хліб", "молоко"]


# --- grounding and the issue texts ------------------------------------------------------------


def test_typed_grounding():
    phone, email, time = _field("phone"), _field("email"), _field("time")
    assert typed.typed_grounded(phone, "+380671234567", "мій 067-123-45-67")
    assert not typed.typed_grounded(phone, "+380671234567", "мій 067-000-00-00")
    assert typed.typed_grounded(email, "a@b.com", "пишіть A@B.com")
    assert typed.typed_grounded(time, "15:30", "давайте о 15 30")
    assert not typed.typed_grounded(time, "16:00", "давайте о 15")
    assert typed.typed_grounded(_field("text"), "x", "x") is None


def test_clarify_text_fills_params_in_the_language():
    assert "30" in typed.clarify_text("date_too_far", {"max": 30}, "uk")
    assert "image, file" in typed.clarify_text(
        "attachment_kind", {"kinds": ["image", "file"]}, "en"
    )
    # an unknown language → English; an unknown param → «?»
    assert typed.clarify_text("number_too_small", {}, "de") == "the number must be at least ?"
