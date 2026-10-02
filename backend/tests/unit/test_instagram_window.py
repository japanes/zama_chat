"""Meta's messaging window, long replies cut into messages, Graph API error kinds."""

from datetime import UTC, datetime, timedelta

import pytest

from app.channels.instagram.client import InstagramError, split_text
from app.channels.instagram.window import SendMode, send_mode, window_closes_at
from app.db.models import MessageRole

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def ago(**delta: float) -> datetime:
    return NOW - timedelta(**delta)


# --- the 24-hour window -----------------------------------------------------------------


@pytest.mark.parametrize("role", [MessageRole.BOT, MessageRole.OPERATOR])
def test_inside_24_hours_anyone_may_write(role):
    assert send_mode(role, ago(hours=23, minutes=59), NOW, human_agent_tag=False) == (
        SendMode.STANDARD
    )
    assert send_mode(role, ago(hours=24), NOW, human_agent_tag=True) == SendMode.STANDARD


def test_the_bot_never_writes_after_24_hours():
    assert send_mode(MessageRole.BOT, ago(hours=24, seconds=1), NOW, human_agent_tag=False) is None
    assert send_mode(MessageRole.BOT, ago(hours=30), NOW, human_agent_tag=True) is None


def test_a_person_has_7_days_with_the_human_agent_tag():
    late = ago(days=3)
    assert send_mode(MessageRole.OPERATOR, late, NOW, human_agent_tag=True) == (
        SendMode.HUMAN_AGENT
    )
    assert send_mode(MessageRole.OPERATOR, late, NOW, human_agent_tag=False) is None
    assert (
        send_mode(MessageRole.OPERATOR, ago(days=7, seconds=1), NOW, human_agent_tag=True) is None
    )


def test_nobody_writes_first():
    assert send_mode(MessageRole.OPERATOR, None, NOW, human_agent_tag=True) is None


def test_window_closes_at():
    assert window_closes_at(None) is None
    assert window_closes_at(ago(hours=1)) == NOW + timedelta(hours=23)


# --- long replies -----------------------------------------------------------------------


def test_short_text_is_one_message():
    assert split_text("  Привіт!  ") == ["Привіт!"]
    assert split_text("   ") == []


def test_long_text_is_cut_at_paragraphs_and_sentences():
    first = "Перший абзац. " * 40  # ~560 characters
    second = "Другий абзац, трохи довший. " * 30  # ~840 characters
    parts = split_text(f"{first.strip()}\n\n{second.strip()}", limit=1000)
    assert len(parts) == 2
    assert parts[0] == first.strip()
    assert parts[1] == second.strip()
    assert all(len(p) <= 1000 for p in parts)


def test_text_without_spaces_is_cut_hard():
    parts = split_text("x" * 2500, limit=1000)
    assert [len(p) for p in parts] == [1000, 1000, 500]


def test_nothing_is_lost_when_cutting():
    text = " ".join(f"слово{i}" for i in range(600))
    parts = split_text(text, limit=300)
    assert all(len(p) <= 300 for p in parts)
    assert " ".join(parts).split() == text.split()


# --- Graph API errors -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (InstagramError(400, "Invalid OAuth access token", code=190), "auth"),
        (InstagramError(401), "auth"),
        (InstagramError(400, "outside", code=10, subcode=2018278), "window"),
        (InstagramError(400, "This message is sent outside of allowed window."), "window"),
        (InstagramError(500), "temporary"),
        (InstagramError(429), "temporary"),
        (InstagramError(400, "Application request limit reached", code=4), "temporary"),
        (InstagramError(400, "An unknown error occurred", code=1), "temporary"),
        (InstagramError(400, "No matching user found", code=100, subcode=2018001), "permanent"),
        (InstagramError(400, "x", kind_hint="auth"), "auth"),
    ],
)
def test_error_kinds(error, kind):
    assert error.kind == kind


def test_short_error_text_has_codes():
    text = InstagramError(400, "Invalid parameter", code=100, subcode=33).short()
    assert text == "Invalid parameter (http 400, code 100, subcode 33)"
