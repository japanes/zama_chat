"""Channel status changes made by the channel itself: a dead token, a token that will
not refresh. The team is told once, when the status changes."""

import uuid
from typing import TYPE_CHECKING

from app.db.models import Channel, ChannelStatus, Tenant
from app.logging import get_logger
from app.notify.service import notify_event

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)


async def set_channel_status(
    services: "Services", channel_id: uuid.UUID, status: ChannelStatus, code: str | None
) -> bool:
    """Set the status (connected / expiring / error) unless the channel is switched off.
    Notifies `channel_error` subscribers when it becomes expiring or error. Returns True
    when the status changed."""
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        channel = await session.get(Channel, channel_id, with_for_update=True)
        if channel is None or channel.status == ChannelStatus.DISABLED:
            return False
        changed = channel.status != status or channel.status_error != code
        if not changed:
            return False
        channel.status = status
        channel.status_error = code
        log.warning("channel.status", channel_id=str(channel_id), status=status, code=code)
        if status in (ChannelStatus.ERROR, ChannelStatus.EXPIRING):
            tenant = await session.get(Tenant, channel.tenant_id)
            admin = services.settings.admin_url.rstrip("/")
            await notify_event(
                session,
                tenant_id=channel.tenant_id,
                event="channel_error",
                values={
                    "channel": channel.title,
                    "error": code or status,
                    "link": f"{admin}/t/{tenant.slug if tenant else ''}/channels",
                },
                now=now,
            )
        return True
