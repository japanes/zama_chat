"""Instagram webhook: signature and parsing of saved payloads (tests/fixtures/instagram)."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.channels.instagram.webhook import attachment_note, parse_webhook, verify_signature

FIXTURES = Path(__file__).parent.parent / "fixtures" / "instagram"
ACCOUNT = "17841400000000001"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- signature --------------------------------------------------------------------------


def test_signature_of_the_raw_body():
    body = (FIXTURES / "message.json").read_bytes()
    assert verify_signature("app-secret", body, sign("app-secret", body))
    # hex in upper case is still the same signature
    upper = "sha256=" + sign("app-secret", body).removeprefix("sha256=").upper()
    assert verify_signature("app-secret", body, upper)


def test_wrong_signatures_are_rejected():
    body = b'{"object":"instagram"}'
    assert not verify_signature("app-secret", body, sign("other-secret", body))
    assert not verify_signature("app-secret", body + b" ", sign("app-secret", body))
    assert not verify_signature("app-secret", body, None)
    assert not verify_signature("app-secret", body, "")
    assert not verify_signature("app-secret", body, "sha1=" + "0" * 40)
    # no secret configured → nothing is trusted
    assert not verify_signature("", body, sign("", body))


# --- parsing ----------------------------------------------------------------------------


def test_client_message():
    parsed = parse_webhook(load("message"))
    assert parsed.skipped == {}
    (event,) = parsed.events
    assert event.account_id == ACCOUNT  # our account is the recipient
    assert event.client_id == "990000000000001"
    assert event.text == "Привіт! Скільки коштує манікюр?"
    assert event.mid.startswith("aWdfZAG1faXRlbTox")
    assert event.echo is False
    assert event.kind == "message"
    assert event.timestamp == datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_echo_swaps_sides():
    (event,) = parse_webhook(load("echo")).events
    assert event.echo is True
    # in an echo our account is the sender; the client is still the client
    assert event.account_id == ACCOUNT
    assert event.client_id == "990000000000001"
    assert event.text == "Добрий день! Манікюр коштує 600 грн."


def test_attachments_postbacks_and_skipped_events():
    parsed = parse_webhook(load("mixed"))
    by_mid = {e.mid: e for e in parsed.events}
    assert list(by_mid) == ["mid.image.1", "mid.reply.1", "mid.postback.1", "mid.other.account"]

    image = by_mid["mid.image.1"]
    assert image.text == ""
    assert image.attachments == [
        {"type": "image", "url": "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1"}
    ]
    assert attachment_note(image.attachments) == "[image]"

    reply = by_mid["mid.reply.1"]
    assert reply.reply_to == {"mid": "mid.bot.question"}
    assert reply.quick_reply == "FRIDAY"

    postback = by_mid["mid.postback.1"]
    assert postback.kind == "postback"
    assert postback.text == "Записатися"  # the button title is what the client "said"

    # several accounts in one POST: each event keeps its own account
    assert by_mid["mid.other.account"].account_id == "17841400000000009"
    assert parsed.skipped == {"read": 1, "reaction": 1, "deleted": 1}


def test_dashboard_test_button_payload():
    (event,) = parse_webhook(load("dashboard_test")).events
    assert (event.account_id, event.client_id, event.text) == ("23245", "12334", "random_text")
    # the test payload has a string timestamp: ignored, not a crash
    assert event.timestamp is None


def test_not_instagram_or_garbage():
    assert parse_webhook({"object": "page", "entry": []}).skipped == {"not_instagram": 1}
    assert parse_webhook([1, 2]).events == []
    parsed = parse_webhook(
        {
            "object": "instagram",
            "entry": [
                "x",
                {
                    "messaging": [
                        {"sender": {"id": "1"}},
                        {"sender": {"id": "1"}, "recipient": {"id": "2"}},
                    ]
                },
                {
                    "messaging": [
                        {"sender": {"id": "1"}, "recipient": {"id": "2"}, "message": {"mid": "m"}}
                    ]
                },
                {
                    "messaging": [
                        {"sender": {"id": "1"}, "recipient": {"id": "2"}, "message": {"text": "t"}}
                    ]
                },
            ],
        }
    )
    assert parsed.events == []
    assert parsed.skipped == {"no_ids": 1, "unknown": 1, "empty": 1, "no_mid": 1}


def test_unsupported_message_is_kept_with_a_note():
    payload = {
        "object": "instagram",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "c1"},
                        "recipient": {"id": ACCOUNT},
                        "message": {"mid": "m1", "is_unsupported": True},
                    }
                ]
            }
        ],
    }
    (event,) = parse_webhook(payload).events
    assert event.attachments == [{"type": "unsupported"}]
    assert attachment_note(event.attachments) == "[unsupported]"


def test_seconds_timestamps_are_understood():
    payload = load("message")
    payload["entry"][0]["messaging"][0]["timestamp"] = 1790942400
    (event,) = parse_webhook(payload).events
    assert event.timestamp == datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
