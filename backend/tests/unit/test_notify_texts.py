"""Notification texts in en / uk / ru."""

import pytest

from app.notify.texts import LANGUAGES, lookup, render

TEMPLATES = ("handover", "choose_reply", "channel_error", "scenario", "test")


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_renders_without_placeholders_left(language, template):
    subject, body = render(language, template, {})
    assert subject
    assert body
    assert "{" not in subject + body


def test_scenario_event_with_field():
    values = {
        "scenario": "Інтерв'ю",
        "event": "field_filled",
        "field": "niche",
        "contact": "Olha (@olha)",
        "channel": "Zama Salon",
        "fields": "niche: манікюр",
        "link": "https://admin.example.com/t/acme/conversations/1",
    }
    subject, body = render("uk", "scenario", values)
    assert "Інтерв'ю" in subject
    assert f"{lookup('uk', 'event.field_filled')} (niche)" in body
    assert "niche: манікюр" in body
    assert body.endswith(values["link"])


def test_scenario_without_answers():
    _, body = render("ru", "scenario", {"scenario": "x", "event": "started"})
    assert lookup("ru", "no_fields") in body


def test_channel_error_is_translated_when_known():
    _, body = render("en", "channel_error", {"channel": "IG", "error": "instagram.token_invalid"})
    assert lookup("en", "error.instagram.token_invalid") in body
    _, body = render("en", "channel_error", {"channel": "IG", "error": "something.new"})
    assert "something.new" in body


def test_client_text_is_quoted_or_marked_empty():
    _, body = render("en", "handover", {"contact": "Olha", "text": "Позовіть людину"})
    assert "Позовіть людину" in body
    _, body = render("en", "handover", {"contact": "Olha"})
    assert lookup("en", "no_text") in body


def test_unknown_language_falls_back_to_english():
    assert render("de", "test", {}) == render("en", "test", {})
