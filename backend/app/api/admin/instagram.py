"""Instagram accounts in the admin: connect through the Instagram login, paste a token
(tests), disconnect, and what to enter in the Meta app dashboard.

The login flow:
  1. GET …/channels/instagram/connect → {url}; a state cookie remembers the tenant;
  2. the browser goes to Instagram, the business account owner allows access;
  3. Instagram redirects to {ADMIN_URL}/api/v1/admin/channels/instagram/callback
     (Nuxt proxies /api/** here) → code → long-lived token → the channel → back to
     /t/<tenant>/channels?connected=<id> or ?error=<code>.

TODO(review): until Meta's App Review approves the app (advanced access to
instagram_business_manage_messages), only accounts added to the app as testers can
be connected.
"""

import secrets
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.admin.access import audit
from app.admin.auth import SESSION_COOKIE, resolve_session
from app.api.admin.common import channel_dict, get_channel
from app.api.admin.deps import AdminDep, TenantAdmin
from app.api.deps import ServicesDep
from app.channels.instagram import connect as ig
from app.channels.instagram.client import InstagramError, TokenInfo
from app.crypto import new_token
from app.db.models import ChannelKind, Tenant, UserRole
from app.errors import AppError, Conflict
from app.logging import get_logger
from app.services import Services

router = APIRouter(prefix="/v1/admin", tags=["admin-instagram"])
log = get_logger(__name__)

STATE_COOKIE = "ig_oauth_state"
STATE_MINUTES = 10
CALLBACK_PATH = "/api/v1/admin/channels/instagram/callback"


def redirect_uri(services: Services) -> str:
    return f"{services.settings.admin_url.rstrip('/')}{CALLBACK_PATH}"


def webhook_url(services: Services) -> str:
    return f"{services.settings.public_api_url.rstrip('/')}/v1/channels/instagram/webhook"


@router.get("/channels/instagram/info")
async def instagram_info(user: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """What is configured and what to enter in the Meta app (no secrets)."""
    s = services.settings
    return {
        "configured": bool(s.meta_app_id and s.meta_app_secret.get_secret_value()),
        "verify_token_set": bool(s.ig_webhook_verify_token.get_secret_value()),
        "webhook_url": webhook_url(services),
        "redirect_uri": redirect_uri(services),
        "human_agent_tag": s.ig_human_agent_tag,
    }


@router.get("/tenants/{tenant_id}/channels/instagram/connect")
async def instagram_connect(access: TenantAdmin, services: ServicesDep) -> JSONResponse:
    client = ig.instagram_client(services)
    state = new_token(24)
    resp = JSONResponse({"url": client.authorize_url(redirect_uri(services), state)})
    resp.set_cookie(
        STATE_COOKIE,
        f"{state}:{access.tenant_id}",
        max_age=STATE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=services.settings.cookie_secure,
        path="/",
    )
    return resp


def _back(services: Services, slug: str | None, query: str) -> RedirectResponse:
    admin = services.settings.admin_url.rstrip("/")
    path = f"/t/{slug}/channels" if slug else "/"
    resp = RedirectResponse(f"{admin}{path}?{query}", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.get("/channels/instagram/callback")
async def instagram_callback(
    request: Request,
    services: ServicesDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    expected, _, tenant_raw = request.cookies.get(STATE_COOKIE, "").partition(":")
    slug: str | None = None
    tenant_id: uuid.UUID | None = None
    try:
        tenant_id = uuid.UUID(tenant_raw)
        async with services.sm() as session:
            tenant = await session.get(Tenant, tenant_id)
            slug = tenant.slug if tenant else None
    except ValueError:
        pass

    def fail(code_: str) -> RedirectResponse:
        return _back(services, slug, f"error={quote(code_)}")

    if error or not code:
        return fail("instagram.cancelled")
    if not expected or not state or not secrets.compare_digest(expected, state):
        return fail("instagram.bad_state")
    user = await resolve_session(services, request.cookies.get(SESSION_COOKIE, ""))
    if user is None or tenant_id is None:
        return fail("auth.required")
    try:
        user.require(tenant_id, UserRole.ADMIN)
        client = ig.instagram_client(services)
        uri = redirect_uri(services)
        short = await client.exchange_code(code, uri)
        token = await client.long_lived(short.token)
        profile = await ig.check_token(client, token.token)
        now = services.clock.now()
        async with services.sm() as session, session.begin():
            channel, created = await ig.save_account(
                session, services, tenant_id=tenant_id, profile=profile, token=token, now=now
            )
            await audit(
                session,
                user,
                "channel.instagram_connect",
                tenant_id=tenant_id,
                target_type="channel",
                target_id=channel.id,
                data={"username": profile.username, "created": created, "via": "login"},
            )
            channel_id = channel.id
    except InstagramError as exc:
        log.warning("instagram.callback_failed", error=exc.short())
        return fail(ig.ig_error(exc).code)
    except AppError as exc:
        return fail(exc.code)
    return _back(services, slug, f"connected={channel_id}")


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=2000)


@router.post("/tenants/{tenant_id}/channels/instagram/token")
async def instagram_token(
    body: TokenIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """A long-lived token pasted by hand (from the Meta dashboard): for tests."""
    client = ig.instagram_client(services)
    token = TokenInfo(token=body.token.strip())
    profile = await ig.check_token(client, token.token)
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        channel, created = await ig.save_account(
            session, services, tenant_id=access.tenant_id, profile=profile, token=token, now=now
        )
        await audit(
            session,
            access.user,
            "channel.instagram_connect",
            tenant_id=access.tenant_id,
            target_type="channel",
            target_id=channel.id,
            data={"username": profile.username, "created": created, "via": "token"},
        )
        return {"channel": channel_dict(channel), "created": created}


@router.post("/tenants/{tenant_id}/channels/{channel_id}/disconnect")
async def instagram_disconnect(
    channel_id: uuid.UUID, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Switch off and forget the token. Conversations and results stay; connecting the
    same account again brings the channel back."""
    async with services.sm() as session, session.begin():
        channel = await get_channel(session, channel_id, access.tenant_id)
        if channel.kind != ChannelKind.INSTAGRAM:
            raise Conflict("channel.kind_mismatch", {"kind": channel.kind})
        await ig.disconnect(session, channel)
        await audit(
            session,
            access.user,
            "channel.instagram_disconnect",
            tenant_id=access.tenant_id,
            target_type="channel",
            target_id=channel.id,
            data={"username": channel.username},
        )
        return {"channel": channel_dict(channel)}
