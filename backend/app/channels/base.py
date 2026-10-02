"""Channel adapters: how a reply leaves the system.

The turn never calls a channel directly: it writes the reply to `messages` with
send_status=pending and queues a `send_message` job in the same transaction (outbox).
The job calls the adapter of the message's channel.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from app.db.models import Channel, Contact, Message


@dataclass
class SendResult:
    external_id: str | None = None  # id the channel gave the message (Instagram mid)
    meta: dict[str, Any] | None = None


class PermanentSendError(Exception):
    """Sending will never succeed (window closed, user blocked the account…)."""


class ChannelAdapter(Protocol):
    # True: the reply is returned in the HTTP response, no send job is needed
    synchronous: bool

    async def send(self, channel: Channel, contact: Contact, message: Message) -> SendResult: ...
