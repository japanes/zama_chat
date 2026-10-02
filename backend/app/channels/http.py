"""HTTP API channel: the reply goes back in the body of POST /v1/messages."""

from datetime import datetime

from app.channels.base import SendResult
from app.db.models import Channel, Contact, Conversation, Message


class HttpChannel:
    synchronous = True

    async def send(
        self, channel: Channel, contact: Contact, message: Message, conversation: Conversation
    ) -> SendResult:
        return SendResult()

    def can_send(self, conversation: Conversation, role: str, now: datetime) -> bool:
        # the caller reads operator messages and reminders by /v1/conversations/history
        return True
