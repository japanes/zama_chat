"""Instagram Direct adapter: replies leave through the send_message job.

A reply longer than 1000 characters goes as several messages; all their mids are kept
in `meta.send.mids`, so their echoes are recognised as ours.
"""

from datetime import datetime

from app.channels.base import ChannelAuthError, PermanentSendError, SendResult
from app.channels.instagram.client import InstagramClient, InstagramError, split_text
from app.channels.instagram.window import SendMode, send_mode
from app.core.clock import Clock
from app.crypto import Crypto
from app.db.models import Channel, ChannelStatus, Contact, Conversation, Message
from app.errors import AppError
from app.settings import Settings


class InstagramChannel:
    synchronous = False

    def __init__(
        self, client: InstagramClient, crypto: Crypto, clock: Clock, settings: Settings
    ) -> None:
        self.client = client
        self.crypto = crypto
        self.clock = clock
        self.settings = settings

    def mode(self, conversation: Conversation, role: str, now: datetime) -> SendMode | None:
        return send_mode(
            role,
            conversation.last_client_at,
            now,
            human_agent_tag=self.settings.ig_human_agent_tag,
        )

    def can_send(self, conversation: Conversation, role: str, now: datetime) -> bool:
        return self.mode(conversation, role, now) is not None

    async def send(
        self, channel: Channel, contact: Contact, message: Message, conversation: Conversation
    ) -> SendResult:
        mode = self.mode(conversation, message.role, self.clock.now())
        if mode is None:
            raise PermanentSendError(
                "outside the 24-hour messaging window", "instagram.window_closed"
            )
        if channel.status == ChannelStatus.DISABLED:
            raise PermanentSendError("the channel is switched off", "channel.disabled")
        if channel.token_enc is None or not channel.account_id:
            raise ChannelAuthError("the channel has no token", "instagram.no_token")
        try:
            token = self.crypto.decrypt(channel.token_enc)
        except AppError as exc:
            raise ChannelAuthError("the token cannot be decrypted", exc.code) from None

        parts = split_text(message.text)
        if not parts:
            raise PermanentSendError("empty message", "send.empty")
        mids: list[str] = []
        for part in parts:
            try:
                data = await self.client.send_text(
                    channel.account_id,
                    token,
                    contact.external_id,
                    part,
                    human_agent=mode == SendMode.HUMAN_AGENT,
                )
            except InstagramError as exc:
                kind = exc.kind
                if kind == "auth":
                    raise ChannelAuthError(exc.short(), "instagram.token_invalid") from None
                if kind == "window":
                    raise PermanentSendError(exc.short(), "instagram.window_closed") from None
                if kind == "permanent":
                    raise PermanentSendError(exc.short(), "instagram.rejected") from None
                # temporary: the job retries; TODO(review): parts already sent go again
                raise
            if data.get("message_id"):
                mids.append(str(data["message_id"]))
        return SendResult(
            external_id=mids[0] if mids else None,
            meta={"mids": mids, "parts": len(parts), "mode": str(mode)},
        )
