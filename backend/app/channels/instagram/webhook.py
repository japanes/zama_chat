"""Instagram webhook: signature check and parsing into flat events.

One POST may carry several entries and several messaging events. Our account is the
`recipient` of a client message and the `sender` of an echo (a copy of every outgoing
message — the bot's replies and what a person typed in the Instagram app).

Kept: text messages, attachments, postbacks (button taps — the title is the text),
echoes. Skipped and counted: reads, reactions, deleted / edited messages, referrals.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SIGNATURE_HEADER = "X-Hub-Signature-256"


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """`X-Hub-Signature-256: sha256=<hex HMAC-SHA256(app secret, raw body)>`."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256=").strip().lower())


@dataclass(frozen=True)
class IgEvent:
    account_id: str  # our Instagram account (IG professional account id)
    client_id: str  # the client's IGSID
    mid: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    echo: bool = False
    kind: str = "message"  # message | postback
    timestamp: datetime | None = None
    reply_to: dict[str, Any] | None = None
    quick_reply: str | None = None


@dataclass
class Parsed:
    events: list[IgEvent] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    # Meta sends milliseconds; old test payloads sometimes seconds
    seconds = value / 1000 if value > 10_000_000_000 else value
    return datetime.fromtimestamp(seconds, tz=UTC)


def attachment_list(raw: Any) -> list[dict[str, Any]]:
    out = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        payload = a.get("payload") if isinstance(a.get("payload"), dict) else {}
        item: dict[str, Any] = {"type": str(a.get("type") or "file")}
        if payload.get("url"):
            item["url"] = payload["url"]
        for key in ("title", "sticker_id", "reel_video_id"):
            if payload.get(key) is not None:
                item[key] = payload[key]
        out.append(item)
    return out


def attachment_note(attachments: list[dict[str, Any]]) -> str:
    """Text for a message without words: the bot still sees that something came."""
    # TODO(review): stage 6 reads images (receipts) and files; until then the bot only
    # knows the type
    return "[" + ", ".join(a.get("type", "file") for a in attachments) + "]"


def _messaging_items(entry: dict[str, Any]) -> list[dict[str, Any]]:
    items = [m for m in entry.get("messaging") or [] if isinstance(m, dict)]
    # the "Test" button of the app dashboard sends `changes` with field=messages
    for change in entry.get("changes") or []:
        if isinstance(change, dict) and change.get("field") == "messages":
            value = change.get("value")
            if isinstance(value, dict):
                items.append(value)
    return items


def _one(item: dict[str, Any], parsed: Parsed) -> None:
    sender = str((item.get("sender") or {}).get("id") or "")
    recipient = str((item.get("recipient") or {}).get("id") or "")
    if not sender or not recipient:
        parsed.skip("no_ids")
        return
    ts = _ts(item.get("timestamp"))

    postback = item.get("postback")
    if isinstance(postback, dict):
        mid = str(postback.get("mid") or "")
        text = str(postback.get("title") or postback.get("payload") or "").strip()
        if not mid or not text:
            parsed.skip("postback_empty")
            return
        parsed.events.append(IgEvent(recipient, sender, mid, text, kind="postback", timestamp=ts))
        return

    message = item.get("message")
    if not isinstance(message, dict):
        # read, reaction, referral, message_edit…
        for key in ("read", "reaction", "referral", "message_edit", "optin"):
            if key in item:
                parsed.skip(key)
                return
        parsed.skip("unknown")
        return
    if message.get("is_deleted"):
        parsed.skip("deleted")
        return
    mid = str(message.get("mid") or "")
    if not mid:
        parsed.skip("no_mid")
        return
    echo = bool(message.get("is_echo"))
    attachments = attachment_list(message.get("attachments"))
    if message.get("is_unsupported"):
        attachments.append({"type": "unsupported"})
    text = str(message.get("text") or "").strip()
    if not text and not attachments:
        parsed.skip("empty")
        return
    quick = message.get("quick_reply")
    reply_to = message.get("reply_to") if isinstance(message.get("reply_to"), dict) else None
    parsed.events.append(
        IgEvent(
            account_id=sender if echo else recipient,
            client_id=recipient if echo else sender,
            mid=mid,
            text=text,
            attachments=attachments,
            echo=echo,
            timestamp=ts,
            reply_to=reply_to,
            quick_reply=str(quick.get("payload")) if isinstance(quick, dict) else None,
        )
    )


def parse_webhook(payload: Any) -> Parsed:
    parsed = Parsed()
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        parsed.skip("not_instagram")
        return parsed
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for item in _messaging_items(entry):
            _one(item, parsed)
    return parsed
