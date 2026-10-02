"""Connect an Instagram account to a tenant: from the login flow or a pasted token.

  token → /me (account id, username, picture) → subscribe to the `messages` webhook
  → channels row (one account belongs to one tenant) → token refresh job.

Network calls run before the transaction; the DB part is short.
"""

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.instagram.client import InstagramClient, InstagramError, Profile, TokenInfo
from app.db.models import Channel, ChannelKind, ChannelStatus
from app.errors import AppError, Conflict
from app.jobs.queue import cancel, enqueue
from app.logging import get_logger

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

# a long-lived token lives 60 days and can be refreshed once it is 24 hours old
REFRESH_BEFORE = timedelta(days=10)
MIN_TOKEN_AGE = timedelta(hours=25)
UNKNOWN_EXPIRY_CHECK = timedelta(days=1)


def instagram_client(services: "Services") -> InstagramClient:
    client = services.extra.get("instagram")
    if client is None or not client.configured:
        raise AppError("instagram.not_configured", status=503)
    return client


def refresh_key(channel_id: uuid.UUID) -> str:
    return f"igrefresh:{channel_id}"


def refresh_at(expires_at: datetime | None, now: datetime) -> datetime:
    if expires_at is None:
        # a pasted token: its age is unknown; the refresh answer tells the expiry
        return now + UNKNOWN_EXPIRY_CHECK
    return max(expires_at - REFRESH_BEFORE, now + MIN_TOKEN_AGE)


async def schedule_refresh(
    session: AsyncSession, channel: Channel, now: datetime, *, at: datetime | None = None
) -> None:
    await enqueue(
        session,
        "refresh_ig_token",
        {"channel_id": str(channel.id)},
        run_at=at or refresh_at(channel.token_expires_at, now),
        tenant_id=channel.tenant_id,
        dedupe_key=refresh_key(channel.id),
        max_attempts=4,
    )


def ig_error(exc: InstagramError) -> AppError:
    """Instagram's answer for the admin: our code + Graph's own words (no token)."""
    code = "instagram.token_invalid" if exc.kind == "auth" else "instagram.api_error"
    return AppError(code, {"detail": exc.short()}, status=502)


async def check_token(client: InstagramClient, token: str) -> Profile:
    try:
        profile = await client.me(token)
        await client.subscribe(token)
    except InstagramError as exc:
        log.warning("instagram.connect_failed", error=exc.short())
        raise ig_error(exc) from None
    return profile


async def save_account(
    session: AsyncSession,
    services: "Services",
    *,
    tenant_id: uuid.UUID,
    profile: Profile,
    token: TokenInfo,
    now: datetime,
) -> tuple[Channel, bool]:
    """Create or update the channel of this account. (channel, created)."""
    channel = (
        await session.execute(
            select(Channel)
            .where(Channel.kind == ChannelKind.INSTAGRAM, Channel.account_id == profile.user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if channel is not None and channel.tenant_id != tenant_id:
        raise Conflict("channel.account_taken", {"username": profile.username or ""})
    created = channel is None
    if channel is None:
        channel = Channel(
            tenant_id=tenant_id,
            kind=ChannelKind.INSTAGRAM,
            title=f"@{profile.username}" if profile.username else (profile.name or "Instagram"),
            account_id=profile.user_id,
            settings={},
        )
        session.add(channel)
    channel.username = profile.username
    channel.avatar_url = profile.picture
    channel.token_enc = services.crypto.encrypt(token.token)
    channel.token_expires_at = (
        now + timedelta(seconds=token.expires_in) if token.expires_in else None
    )
    channel.status = ChannelStatus.CONNECTED
    channel.status_error = None
    await session.flush()
    await schedule_refresh(session, channel, now)
    return channel, created


async def disconnect(session: AsyncSession, channel: Channel) -> None:
    """Switch the channel off and forget the token. Conversations and results stay."""
    channel.status = ChannelStatus.DISABLED
    channel.status_error = None
    channel.token_enc = None
    channel.token_expires_at = None
    await cancel(session, refresh_key(channel.id))
