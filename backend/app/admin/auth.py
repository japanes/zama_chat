"""Passwordless sign-in to the admin: a link sent by email, or Google.

Only users that already exist can sign in (invited from the admin or created with
`zama create-user`). A successful sign-in creates a session: the browser keeps the
token in the httpOnly cookie `zama_session`, the DB only its sha256.

Email link: `token_urlsafe(48)`, sha256 in `login_tokens`, works once and for
`magic_link_minutes`, at most one link per address per minute. The answer is always
`{ok: true}`, so nobody can find out which addresses exist.

Google: the code is exchanged for a token on the backend (the secret stays here); the
account is matched by `google_sub`, the first time by a verified email.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.access import AdminUser
from app.admin.mailer import Mailer, magic_link_mail
from app.crypto import new_token, sha256_hex
from app.db.models import AdminSession, LoginToken, User
from app.errors import AppError, Forbidden
from app.logging import get_logger
from app.services import Services

log = get_logger(__name__)

SESSION_COOKIE = "zama_session"
STATE_COOKIE = "g_oauth_state"
STATE_MINUTES = 10
MAGIC_LINK_RATE = timedelta(minutes=1)
# a session used within this time is not written again (one UPDATE per hour at most)
SESSION_TOUCH = timedelta(hours=1)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 320:
        raise AppError("auth.email_invalid")
    return email


async def active_user_by_email(session: AsyncSession, email: str) -> User | None:
    return (
        await session.execute(select(User).where(User.email == email, User.is_active))
    ).scalar_one_or_none()


# --- email link -------------------------------------------------------------------------


async def send_magic_link(services: Services, email: str, *, locale: str | None) -> str | None:
    """Send the sign-in link. Returns the link only in dev with MAGIC_LINK_DEV_ECHO=1
    (no mail server needed there); otherwise None — also for an unknown address."""
    settings = services.settings
    mailer: Mailer | None = services.extra.get("mailer")
    dev_echo = settings.magic_link_dev_echo and settings.is_dev
    if mailer is None and not dev_echo:
        raise AppError("auth.mail_not_configured", status=503)
    email = normalize_email(email)
    now = services.clock.now()
    minutes = settings.magic_link_minutes
    token = new_token(48)
    async with services.sm() as session, session.begin():
        recent = (
            await session.execute(
                select(LoginToken.id)
                .where(LoginToken.email == email, LoginToken.created_at > now - MAGIC_LINK_RATE)
                .limit(1)
            )
        ).scalar_one_or_none()
        # the limit keeps a mailbox from being flooded; with the dev echo the link is shown
        # on the page and a throttled request would show "check your inbox" with no link
        if recent is not None and not dev_echo:
            log.info("auth.magic_link_throttled")
            return None
        user = await active_user_by_email(session, email)
        if user is None:
            log.info("auth.magic_link_unknown_address")
            return None
        row = LoginToken(
            token_hash=sha256_hex(token),
            email=email,
            expires_at=now + timedelta(minutes=minutes),
            created_at=now,
        )
        session.add(row)
        await session.flush()
        token_id = row.id
        language = user.locale or locale or "en"

    link = f"{settings.admin_url.rstrip('/')}/auth/magic?token={token}"
    if mailer is not None:
        try:
            await mailer.send(magic_link_mail(email, link, language=language, minutes=minutes))
        except Exception as exc:
            # a link nobody received must not stay valid
            async with services.sm() as session, session.begin():
                await session.execute(delete(LoginToken).where(LoginToken.id == token_id))
            log.exception("auth.magic_link_send_failed")
            raise AppError("auth.mail_failed", status=502) from exc
    if dev_echo:
        log.info("auth.magic_link_dev_echo", link=link)
        return link
    return None


async def verify_magic_link(
    services: Services, token: str, *, user_agent: str | None, ip: str | None
) -> tuple[str, AdminUser]:
    """Spend the link → (session token, user)."""
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        row = (
            await session.execute(
                select(LoginToken)
                .where(LoginToken.token_hash == sha256_hex(token or ""))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.used_at is not None:
            raise AppError("auth.bad_token")
        if row.expires_at <= now:
            raise AppError("auth.link_expired")
        row.used_at = now
        user = await active_user_by_email(session, row.email)
        if user is None:
            raise Forbidden("auth.no_access")
        user.last_login_at = now
        raw = await create_session(
            session, user.id, now, services.settings.session_days, user_agent=user_agent, ip=ip
        )
        return raw, await AdminUser.load(session, user)


# --- sessions ---------------------------------------------------------------------------


async def create_session(
    session: AsyncSession,
    user_id: uuid.UUID,
    now: datetime,
    days: int,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    raw = new_token(32)
    session.add(
        AdminSession(
            token_hash=sha256_hex(raw),
            user_id=user_id,
            expires_at=now + timedelta(days=days),
            last_seen_at=now,
            user_agent=(user_agent or "")[:500] or None,
            ip=(ip or "")[:64] or None,
            created_at=now,
        )
    )
    await session.flush()
    return raw


async def resolve_session(services: Services, raw: str) -> AdminUser | None:
    """Cookie token → the user; None when unknown, expired or the user is switched off.
    Moves the expiry forward (at most once per SESSION_TOUCH)."""
    if not raw:
        return None
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        row = (
            await session.execute(
                select(AdminSession, User)
                .join(User, User.id == AdminSession.user_id)
                .where(AdminSession.token_hash == sha256_hex(raw))
            )
        ).first()
        if row is None:
            return None
        admin_session, user = row
        if admin_session.expires_at <= now or not user.is_active:
            return None
        if now - admin_session.last_seen_at > SESSION_TOUCH:
            admin_session.last_seen_at = now
            admin_session.expires_at = now + timedelta(days=services.settings.session_days)
        return await AdminUser.load(session, user)


async def end_session(services: Services, raw: str) -> None:
    if not raw:
        return
    async with services.sm() as session, session.begin():
        await session.execute(
            delete(AdminSession).where(AdminSession.token_hash == sha256_hex(raw))
        )


async def end_user_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    """A switched-off user is signed out everywhere."""
    await session.execute(delete(AdminSession).where(AdminSession.user_id == user_id))


# --- Google -----------------------------------------------------------------------------


@dataclass(frozen=True)
class GoogleProfile:
    sub: str
    email: str
    email_verified: bool
    name: str | None
    picture: str | None


def google_redirect_uri(services: Services) -> str:
    # through the admin's /api proxy: the cookies belong to the admin's domain
    return f"{services.settings.admin_url.rstrip('/')}/api/v1/admin/auth/google/callback"


def google_start_url(services: Services, state: str) -> str:
    settings = services.settings
    if not settings.google_client_id:
        raise AppError("auth.google_not_configured", status=503)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": google_redirect_uri(services),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def google_profile(services: Services, code: str) -> GoogleProfile:
    settings = services.settings
    try:
        resp = await services.http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "redirect_uri": google_redirect_uri(services),
                "grant_type": "authorization_code",
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            log.warning("auth.google_token_failed", status=resp.status_code)
            raise AppError("auth.google_failed")
        access_token = resp.json().get("access_token")
        if not access_token:
            raise AppError("auth.google_failed")
        info = await services.http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        if info.status_code != 200:
            log.warning("auth.google_userinfo_failed", status=info.status_code)
            raise AppError("auth.google_failed")
        data = info.json()
    except AppError:
        raise
    except Exception as exc:
        log.warning("auth.google_unavailable", error=type(exc).__name__)
        raise AppError("auth.google_failed") from exc
    if not data.get("sub") or not data.get("email"):
        raise AppError("auth.google_failed")
    return GoogleProfile(
        sub=str(data["sub"]),
        email=str(data["email"]).strip().lower(),
        email_verified=bool(data.get("email_verified")),
        name=data.get("name"),
        picture=data.get("picture"),
    )


async def google_login(
    services: Services, profile: GoogleProfile, *, user_agent: str | None, ip: str | None
) -> tuple[str, AdminUser]:
    """Google profile → (session token, user). The first sign-in binds `google_sub` to
    the user with the same (verified) email."""
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        user = (
            await session.execute(select(User).where(User.google_sub == profile.sub))
        ).scalar_one_or_none()
        if user is None:
            if not profile.email_verified:
                raise Forbidden("auth.google_email_unverified")
            user = await active_user_by_email(session, profile.email)
            if user is None or (user.google_sub and user.google_sub != profile.sub):
                raise Forbidden("auth.no_access")
            user.google_sub = profile.sub
        if not user.is_active:
            raise Forbidden("auth.no_access")
        user.name = user.name or profile.name
        user.avatar_url = profile.picture or user.avatar_url
        user.last_login_at = now
        raw = await create_session(
            session, user.id, now, services.settings.session_days, user_agent=user_agent, ip=ip
        )
        return raw, await AdminUser.load(session, user)
