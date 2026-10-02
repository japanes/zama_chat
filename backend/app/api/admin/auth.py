"""Admin sign-in: email link, Google, logout, the current user.

The admin calls these through its own `/api` proxy, so the cookies belong to the
admin's domain. Google sends the browser back to `<admin>/api/v1/admin/auth/google/
callback`; errors there end on `<admin>/login?error=<code>`.
"""

import re
import secrets
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admin.access import AdminUser
from app.admin.auth import (
    SESSION_COOKIE,
    STATE_COOKIE,
    STATE_MINUTES,
    end_session,
    google_login,
    google_profile,
    google_start_url,
    send_magic_link,
    verify_magic_link,
)
from app.api.admin.deps import AdminDep, client_ip
from app.api.deps import ServicesDep
from app.crypto import new_token
from app.db.models import Membership, Tenant, User
from app.errors import AppError
from app.services import Services

router = APIRouter(prefix="/v1/admin/auth", tags=["admin-auth"])

LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


def set_session_cookie(response: Response, services: Services, raw: str) -> None:
    settings = services.settings
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def _safe_next(value: str | None) -> str:
    """Only a path inside the admin: no other host after sign-in."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value[:500]


def _admin_redirect(services: Services, path: str) -> RedirectResponse:
    return RedirectResponse(f"{services.settings.admin_url.rstrip('/')}{path}", status_code=302)


def _login_error(services: Services, code: str) -> RedirectResponse:
    resp = _admin_redirect(services, f"/login?error={quote(code)}")
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


class MagicLinkIn(BaseModel):
    email: str = Field(max_length=320)
    locale: str | None = Field(default=None, max_length=16)


@router.post("/magic-link")
async def magic_link(body: MagicLinkIn, services: ServicesDep) -> dict[str, Any]:
    link = await send_magic_link(services, body.email, locale=body.locale)
    out: dict[str, Any] = {"ok": True}
    if link is not None:
        out["dev_link"] = link
    return out


class VerifyIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


@router.post("/magic-link/verify")
async def magic_link_verify(
    body: VerifyIn, request: Request, response: Response, services: ServicesDep
) -> dict[str, Any]:
    raw, user = await verify_magic_link(
        services,
        body.token,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )
    set_session_cookie(response, services, raw)
    return {"user": user.to_dict()}


@router.get("/google/start")
async def google_start(services: ServicesDep, next: str | None = None) -> RedirectResponse:
    state = new_token(24)
    try:
        url = google_start_url(services, state)
    except AppError as exc:
        return _login_error(services, exc.code)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        STATE_COOKIE,
        f"{state}:{_safe_next(next)}",
        max_age=STATE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=services.settings.cookie_secure,
        path="/",
    )
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request,
    services: ServicesDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    expected, _, next_path = request.cookies.get(STATE_COOKIE, "").partition(":")
    if error or not code:
        return _login_error(services, "auth.google_cancelled")
    if not expected or not state or not secrets.compare_digest(expected, state):
        return _login_error(services, "auth.google_state")
    try:
        profile = await google_profile(services, code)
        raw, _ = await google_login(
            services,
            profile,
            user_agent=request.headers.get("user-agent"),
            ip=client_ip(request),
        )
    except AppError as exc:
        return _login_error(services, exc.code)
    resp = _admin_redirect(services, _safe_next(next_path))
    resp.delete_cookie(STATE_COOKIE, path="/")
    set_session_cookie(resp, services, raw)
    return resp


@router.post("/logout")
async def logout(request: Request, response: Response, services: ServicesDep) -> dict[str, Any]:
    await end_session(services, request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


async def _tenants_of(services: Services, user_id, is_superadmin: bool) -> list[dict[str, Any]]:
    async with services.sm() as session:
        if is_superadmin:
            rows = (
                await session.execute(select(Tenant).order_by(Tenant.title, Tenant.slug))
            ).scalars()
            return [
                {
                    "id": str(t.id),
                    "slug": t.slug,
                    "title": t.title,
                    "role": "owner",
                    "is_active": t.is_active,
                }
                for t in rows
            ]
        rows = (
            await session.execute(
                select(Tenant, Membership.role)
                .join(Membership, Membership.tenant_id == Tenant.id)
                .where(Membership.user_id == user_id)
                .order_by(Tenant.title, Tenant.slug)
            )
        ).all()
        return [
            {
                "id": str(t.id),
                "slug": t.slug,
                "title": t.title,
                "role": role,
                "is_active": t.is_active,
            }
            for t, role in rows
        ]


@router.get("/me")
async def me(user: AdminDep, services: ServicesDep) -> dict[str, Any]:
    return {
        "user": user.to_dict(),
        "tenants": await _tenants_of(services, user.id, user.is_superadmin),
    }


class MeIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    locale: str | None = Field(default=None, max_length=16)


@router.patch("/me")
async def update_me(body: MeIn, user: AdminDep, services: ServicesDep) -> dict[str, Any]:
    if body.locale is not None and not LOCALE_RE.match(body.locale):
        raise AppError("user.locale_invalid", {"locale": body.locale})
    async with services.sm() as session, session.begin():
        row = await session.get(User, user.id)
        assert row is not None
        if "name" in body.model_fields_set:
            row.name = (body.name or "").strip() or None
        if body.locale is not None:
            row.locale = body.locale
        updated = await AdminUser.load(session, row)
    return {"user": updated.to_dict()}
