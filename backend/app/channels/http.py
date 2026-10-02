"""HTTP API channel: the reply goes back in the body of POST /v1/messages."""

from app.channels.base import SendResult
from app.db.models import Channel, Contact, Message


class HttpChannel:
    synchronous = True

    async def send(self, channel: Channel, contact: Contact, message: Message) -> SendResult:
        return SendResult()
