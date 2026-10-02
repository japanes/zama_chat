"""Channel adapters: how a reply leaves the system.

The turn never calls a channel directly: it writes the reply to `messages` with
send_status=pending and queues a `send_message` job in the same transaction (outbox).
The job calls the adapter of the message's channel.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from app.db.models import Channel, Contact, Conversation, Message


@dataclass
class SendResult:
    external_id: str | None = None  # id the channel gave the message (Instagram mid)
    meta: dict[str, Any] | None = None


class PermanentSendError(Exception):
    """Sending will never succeed (window closed, user blocked the account…).

    `code` is a short machine reason shown in the admin (`instagram.window_closed`)."""

    def __init__(self, message: str, code: str = "send.failed") -> None:
        super().__init__(message)
        self.code = code


class ChannelAuthError(PermanentSendError):
    """The channel's token is no longer valid: the channel needs reconnecting."""


class ChannelAdapter(Protocol):
    # True: the reply is returned in the HTTP response, no send job is needed
    synchronous: bool

    async def send(
        self, channel: Channel, contact: Contact, message: Message, conversation: Conversation
    ) -> SendResult: ...

    def can_send(self, conversation: Conversation, role: str, now: Any) -> bool:
        """Whether a message of `role` (bot / operator) may go to the client now
        (Instagram: the 24-hour window)."""
        ...
