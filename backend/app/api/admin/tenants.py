"""Tenants, channels, members, users, API keys, endpoints and the audit log.

Who may do what:
- operator — sees the tenant and its channels;
- admin — tenant settings, channels, API keys, endpoints;
- owner — members;
- superadmin — creates tenants, switches them off, manages all users.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select

from app.admin.access import audit
from app.admin.auth import end_user_sessions, normalize_email
from app.admin.tenants import create_tenant, ensure_user, issue_api_key, set_membership
from app.api.admin.common import (
    Page,
    channel_dict,
    get_channel,
    get_tenant,
    iso,
    page_params,
    sid,
    tenant_row,
)
from app.api.admin.deps import (
    AdminDep,
    SuperDep,
    TenantAdmin,
    TenantOperator,
    TenantOwner,
)
from app.api.deps import ServicesDep
from app.core.tenant_settings import CHANNEL_OVERRIDABLE, TenantSettings, effective_settings
from app.crypto import new_token
from app.db.models import (
    ApiKey,
    AuditLog,
    Channel,
    ChannelKind,
    ChannelStatus,
    Endpoint,
    Membership,
    Tenant,
    User,
    UserRole,
)
from app.errors import AppError, Conflict, NotFound
from app.operator.variants import MAX_VARIANTS, MIN_VARIANTS

router = APIRouter(prefix="/v1/admin", tags=["admin-tenants"])

PageDep = Annotated[Page, Depends(page_params)]

# channel settings that are not tenant overrides
CHANNEL_OWN_SETTINGS = ("operator_mode", "operator_variants", "merge_pause_s", "merge_max_wait_s")


def tenant_dict(t: Tenant, role: str | None = None) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "slug": t.slug,
        "title": t.title,
        "is_active": t.is_active,
        "settings": t.settings or {},
        "role": role,
        "created_at": iso(t.created_at),
    }


# --- tenants ------------------------------------------------------------------------------


@router.get("/tenants")
async def list_tenants(user: AdminDep, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        query = select(Tenant).order_by(Tenant.title, Tenant.slug)
        if not user.is_superadmin:
            query = query.where(Tenant.id.in_(list(user.roles)))
        rows = (await session.execute(query)).scalars()
        return {"items": [tenant_dict(t, user.role_in(t.id)) for t in rows]}


class TenantCreateIn(BaseModel):
    slug: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    settings: dict[str, Any] = Field(default_factory=dict)


@router.post("/tenants")
async def create_tenant_route(
    body: TenantCreateIn, user: SuperDep, services: ServicesDep
) -> dict[str, Any]:
    settings = TenantSettings.model_validate({"company_title": body.title, **body.settings})
    async with services.sm() as session, session.begin():
        tenant, channel = await create_tenant(
            session, slug=body.slug, title=body.title, settings=settings
        )
        await audit(
            session,
            user,
            "tenant.create",
            tenant_id=tenant.id,
            target_type="tenant",
            target_id=tenant.id,
            data={"slug": body.slug},
        )
        return {"tenant": tenant_dict(tenant, "owner"), "channel": channel_dict(channel)}


@router.get("/tenants/{tenant_id}")
async def get_tenant_route(access: TenantOperator, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        tenant = await get_tenant(session, access.tenant_id)
        channels = (
            await session.execute(
                select(Channel).where(Channel.tenant_id == tenant.id).order_by(Channel.created_at)
            )
        ).scalars()
        return {
            "tenant": tenant_dict(tenant, access.role),
            "channels": [channel_dict(c) for c in channels],
            # defaults filled in: what the bot really uses
            "effective_settings": TenantSettings.model_validate(tenant.settings or {}).model_dump(
                mode="json"
            ),
        }


class TenantPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    settings: dict[str, Any] | None = None
    is_active: bool | None = None


@router.patch("/tenants/{tenant_id}")
async def patch_tenant(
    body: TenantPatchIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    if body.is_active is not None:
        access.user.require_superadmin()
    async with services.sm() as session, session.begin():
        tenant = await get_tenant(session, access.tenant_id)
        changes: dict[str, Any] = {}
        if body.title is not None:
            tenant.title = body.title
            changes["title"] = body.title
        if body.settings is not None:
            # the whole settings object is replaced; checked before saving
            settings = TenantSettings.model_validate(body.settings).validate_rules()
            before = TenantSettings.model_validate(tenant.settings or {})
            tenant.settings = settings.model_dump(mode="json")
            changes["settings"] = tenant.settings
            if (before.receipt_review, before.receipt_review_above) != (
                settings.receipt_review,
                settings.receipt_review_above,
            ):
                # docs/03 §8: switching the human check of receipts is logged on its own
                await audit(
                    session,
                    access.user,
                    "tenant.receipt_review",
                    tenant_id=tenant.id,
                    target_type="tenant",
                    target_id=tenant.id,
                    data={
                        "from": before.receipt_review,
                        "to": settings.receipt_review,
                        "above": {k: str(v) for k, v in settings.receipt_review_above.items()},
                    },
                )
            # a channel override must still fit the new tenant languages
            channels = (
                await session.execute(select(Channel).where(Channel.tenant_id == tenant.id))
            ).scalars()
            for channel in channels:
                effective_settings(tenant.settings, channel.settings).validate_rules()
        if body.is_active is not None:
            tenant.is_active = body.is_active
            changes["is_active"] = body.is_active
        await audit(
            session,
            access.user,
            "tenant.update",
            tenant_id=tenant.id,
            target_type="tenant",
            target_id=tenant.id,
            data=changes,
        )
        return {"tenant": tenant_dict(tenant, access.role)}


# --- channels -----------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/channels")
async def list_channels(access: TenantOperator, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(Channel)
                .where(Channel.tenant_id == access.tenant_id)
                .order_by(Channel.created_at)
            )
        ).scalars()
        return {"items": [channel_dict(c) for c in rows]}


class ChannelPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    # only "disabled" ⇄ "connected" by hand; other statuses are set by the channel itself
    disabled: bool | None = None
    settings: dict[str, Any] | None = None


def _check_channel_settings(tenant: Tenant, data: dict[str, Any]) -> dict[str, Any]:
    allowed = set(CHANNEL_OVERRIDABLE) | set(CHANNEL_OWN_SETTINGS)
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise AppError("channel.settings_unknown", {"keys": unknown, "allowed": sorted(allowed)})
    for key in ("merge_pause_s", "merge_max_wait_s"):
        value = data.get(key)
        if value is not None and (not isinstance(value, int | float) or not 0 <= value <= 600):
            raise AppError("channel.setting_invalid", {"key": key, "value": value})
    if "operator_mode" in data and not isinstance(data["operator_mode"], bool):
        raise AppError("channel.setting_invalid", {"key": "operator_mode"})
    variants = data.get("operator_variants")
    if variants is not None and (
        isinstance(variants, bool)
        or not isinstance(variants, int)
        or not MIN_VARIANTS <= variants <= MAX_VARIANTS
    ):
        raise AppError("channel.setting_invalid", {"key": "operator_variants", "value": variants})
    effective_settings(tenant.settings, data).validate_rules()
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


@router.patch("/tenants/{tenant_id}/channels/{channel_id}")
async def patch_channel(
    channel_id: uuid.UUID, body: ChannelPatchIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        tenant = await get_tenant(session, access.tenant_id)
        channel = await get_channel(session, channel_id, access.tenant_id)
        changes: dict[str, Any] = {}
        if body.title is not None:
            channel.title = changes["title"] = body.title
        no_token = channel.kind == ChannelKind.INSTAGRAM and channel.token_enc is None
        if body.disabled is False and no_token:
            # a disconnected account comes back only through connecting it again
            raise Conflict("instagram.no_token")
        if body.disabled is not None:
            channel.status = ChannelStatus.DISABLED if body.disabled else ChannelStatus.CONNECTED
            changes["status"] = channel.status
        if body.settings is not None:
            channel.settings = changes["settings"] = _check_channel_settings(tenant, body.settings)
        await audit(
            session,
            access.user,
            "channel.update",
            tenant_id=tenant.id,
            target_type="channel",
            target_id=channel.id,
            data=changes,
        )
        return {"channel": channel_dict(channel)}


# --- members (per tenant) -----------------------------------------------------------------


def member_dict(m: Membership, u: User) -> dict[str, Any]:
    return {
        "user_id": str(u.id),
        "email": u.email,
        "name": u.name,
        "avatar_url": u.avatar_url,
        "role": m.role,
        "is_active": u.is_active,
        "last_login_at": iso(u.last_login_at),
    }


@router.get("/tenants/{tenant_id}/members")
async def list_members(access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(Membership.tenant_id == access.tenant_id)
                .order_by(User.email)
            )
        ).all()
        return {"items": [member_dict(m, u) for m, u in rows]}


class MemberIn(BaseModel):
    email: str = Field(max_length=320)
    role: UserRole
    name: str | None = Field(default=None, max_length=200)


async def _owners_left(session, tenant_id: uuid.UUID, without: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.role == UserRole.OWNER,
                Membership.user_id != without,
            )
        )
    ).scalar_one()


@router.post("/tenants/{tenant_id}/members")
async def add_member(body: MemberIn, access: TenantOwner, services: ServicesDep) -> dict[str, Any]:
    """Invite by email: the user is created when missing and signs in with the email
    link or Google. No email is sent here — TODO(review): send an invitation email."""
    email = normalize_email(body.email)
    async with services.sm() as session, session.begin():
        await get_tenant(session, access.tenant_id)
        user, created = await ensure_user(session, email, name=body.name)
        if not user.is_active:
            raise Conflict("user.inactive", {"email": email})
        member = await set_membership(
            session, user_id=user.id, tenant_id=access.tenant_id, role=body.role
        )
        await audit(
            session,
            access.user,
            "member.set",
            tenant_id=access.tenant_id,
            target_type="user",
            target_id=user.id,
            data={"email": email, "role": body.role.value, "created": created},
        )
        return {"member": member_dict(member, user), "created": created}


class MemberPatchIn(BaseModel):
    role: UserRole


async def _member(session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Membership, User]:
    row = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
        )
    ).first()
    if row is None:
        raise NotFound("member.not_found", {"user_id": str(user_id)})
    return row[0], row[1]


@router.patch("/tenants/{tenant_id}/members/{user_id}")
async def patch_member(
    user_id: uuid.UUID, body: MemberPatchIn, access: TenantOwner, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        member, user = await _member(session, access.tenant_id, user_id)
        if (
            member.role == UserRole.OWNER
            and body.role != UserRole.OWNER
            and await _owners_left(session, access.tenant_id, user_id) == 0
        ):
            raise Conflict("member.last_owner")
        member.role = body.role.value
        await audit(
            session,
            access.user,
            "member.set",
            tenant_id=access.tenant_id,
            target_type="user",
            target_id=user_id,
            data={"email": user.email, "role": body.role.value},
        )
        return {"member": member_dict(member, user)}


@router.delete("/tenants/{tenant_id}/members/{user_id}")
async def remove_member(
    user_id: uuid.UUID, access: TenantOwner, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        member, user = await _member(session, access.tenant_id, user_id)
        if (
            member.role == UserRole.OWNER
            and await _owners_left(session, access.tenant_id, user_id) == 0
        ):
            raise Conflict("member.last_owner")
        await session.delete(member)
        await audit(
            session,
            access.user,
            "member.remove",
            tenant_id=access.tenant_id,
            target_type="user",
            target_id=user_id,
            data={"email": user.email},
        )
        return {"ok": True}


# --- users (superadmin) -------------------------------------------------------------------


def user_dict(u: User, memberships: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "email": u.email,
        "name": u.name,
        "avatar_url": u.avatar_url,
        "is_superadmin": u.is_superadmin,
        "is_active": u.is_active,
        "locale": u.locale,
        "has_google": u.google_sub is not None,
        "last_login_at": iso(u.last_login_at),
        "created_at": iso(u.created_at),
        "memberships": memberships or [],
    }


@router.get("/users")
async def list_users(
    _: SuperDep, services: ServicesDep, page: PageDep, q: str | None = None
) -> dict[str, Any]:
    async with services.sm() as session:
        query = select(User)
        if q:
            query = query.where(User.email.ilike(f"%{q.strip().lower()}%"))
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        users = list(
            (
                await session.execute(
                    query.order_by(User.email).limit(page.limit).offset(page.offset)
                )
            ).scalars()
        )
        rows = (
            await session.execute(
                select(Membership, Tenant)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(Membership.user_id.in_([u.id for u in users]))
            )
        ).all()
        by_user: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for m, t in rows:
            by_user.setdefault(m.user_id, []).append(
                {"tenant_id": str(t.id), "slug": t.slug, "title": t.title, "role": m.role}
            )
        return {"items": [user_dict(u, by_user.get(u.id)) for u in users], "total": total}


class UserCreateIn(BaseModel):
    email: str = Field(max_length=320)
    name: str | None = Field(default=None, max_length=200)
    is_superadmin: bool = False
    tenant_id: uuid.UUID | None = None
    role: UserRole | None = None


@router.post("/users")
async def create_user(body: UserCreateIn, user: SuperDep, services: ServicesDep) -> dict[str, Any]:
    email = normalize_email(body.email)
    async with services.sm() as session, session.begin():
        row, created = await ensure_user(session, email, name=body.name)
        if not created:
            raise Conflict("user.exists", {"email": email})
        row.is_superadmin = body.is_superadmin
        if body.tenant_id is not None:
            await get_tenant(session, body.tenant_id)
            await set_membership(
                session, user_id=row.id, tenant_id=body.tenant_id, role=body.role or UserRole.ADMIN
            )
        await audit(
            session,
            user,
            "user.create",
            tenant_id=body.tenant_id,
            target_type="user",
            target_id=row.id,
            data={"email": email, "is_superadmin": body.is_superadmin},
        )
        return {"user": user_dict(row)}


class UserPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None
    is_superadmin: bool | None = None


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: uuid.UUID, body: UserPatchIn, user: SuperDep, services: ServicesDep
) -> dict[str, Any]:
    if user_id == user.id and (body.is_active is False or body.is_superadmin is False):
        # nobody locks themselves out (and the last superadmin stays)
        raise Conflict("user.cannot_demote_self")
    async with services.sm() as session, session.begin():
        row = await session.get(User, user_id)
        if row is None:
            raise NotFound("user.not_found", {"id": str(user_id)})
        changes: dict[str, Any] = {}
        if "name" in body.model_fields_set:
            row.name = changes["name"] = (body.name or "").strip() or None
        if body.is_superadmin is not None:
            row.is_superadmin = changes["is_superadmin"] = body.is_superadmin
        if body.is_active is not None:
            row.is_active = changes["is_active"] = body.is_active
            if not body.is_active:
                await end_user_sessions(session, row.id)
        await audit(
            session, user, "user.update", target_type="user", target_id=row.id, data=changes
        )
        return {"user": user_dict(row)}


# --- API keys -----------------------------------------------------------------------------


def key_dict(k: ApiKey, channel_title: str | None = None) -> dict[str, Any]:
    return {
        "id": str(k.id),
        "title": k.title,
        "prefix": k.prefix,
        "channel_id": str(k.channel_id),
        "channel_title": channel_title,
        "created_at": iso(k.created_at),
        "last_used_at": iso(k.last_used_at),
        "revoked_at": iso(k.revoked_at),
    }


@router.get("/tenants/{tenant_id}/api-keys")
async def list_api_keys(access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(ApiKey, Channel.title)
                .join(Channel, Channel.id == ApiKey.channel_id)
                .where(ApiKey.tenant_id == access.tenant_id)
                .order_by(ApiKey.created_at.desc())
            )
        ).all()
        return {"items": [key_dict(k, title) for k, title in rows]}


class ApiKeyIn(BaseModel):
    title: str = Field(default="", max_length=200)
    channel_id: uuid.UUID | None = None


@router.post("/tenants/{tenant_id}/api-keys")
async def create_api_key(
    body: ApiKeyIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        if body.channel_id is not None:
            await get_channel(session, body.channel_id, access.tenant_id)
        key, raw = await issue_api_key(
            session, tenant_id=access.tenant_id, channel_id=body.channel_id, title=body.title
        )
        await audit(
            session,
            access.user,
            "api_key.create",
            tenant_id=access.tenant_id,
            target_type="api_key",
            target_id=key.id,
            data={"title": body.title},
        )
        # the raw key is shown once and never stored
        return {"api_key": key_dict(key), "key": raw}


@router.delete("/tenants/{tenant_id}/api-keys/{key_id}")
async def revoke_api_key(
    key_id: uuid.UUID, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        key = await tenant_row(session, ApiKey, key_id, access.tenant_id, "api_key.not_found")
        if key.revoked_at is None:
            key.revoked_at = services.clock.now()
            await audit(
                session,
                access.user,
                "api_key.revoke",
                tenant_id=access.tenant_id,
                target_type="api_key",
                target_id=key.id,
            )
        return {"api_key": key_dict(key)}


# --- endpoints (where results are sent) ---------------------------------------------------


def endpoint_dict(e: Endpoint) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "name": e.name,
        "url": e.url,
        "is_active": e.is_active,
        "has_secret": e.secret_enc is not None,
        # values may hold tokens: only the names go back to the browser
        "header_names": sorted((e.headers or {}).keys()),
        "created_at": iso(e.created_at),
        "updated_at": iso(e.updated_at),
    }


ENDPOINT_NAME = r"^[a-z0-9_][a-z0-9_-]{0,99}$"


class EndpointIn(BaseModel):
    name: str = Field(pattern=ENDPOINT_NAME)
    url: HttpUrl
    # null → a secret is generated and shown once
    secret: str | None = Field(default=None, min_length=16, max_length=500)
    headers: dict[str, str] = Field(default_factory=dict)
    is_active: bool = True


@router.get("/tenants/{tenant_id}/endpoints")
async def list_endpoints(access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(Endpoint)
                .where(Endpoint.tenant_id == access.tenant_id)
                .order_by(Endpoint.name)
            )
        ).scalars()
        return {"items": [endpoint_dict(e) for e in rows]}


@router.post("/tenants/{tenant_id}/endpoints")
async def create_endpoint(
    body: EndpointIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    secret = body.secret or new_token(32)
    async with services.sm() as session, session.begin():
        exists = (
            await session.execute(
                select(Endpoint.id).where(
                    Endpoint.tenant_id == access.tenant_id, Endpoint.name == body.name
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise Conflict("endpoint.name_taken", {"name": body.name})
        row = Endpoint(
            tenant_id=access.tenant_id,
            name=body.name,
            url=str(body.url),
            secret_enc=services.crypto.encrypt(secret),
            headers=body.headers,
            is_active=body.is_active,
        )
        session.add(row)
        await session.flush()
        await audit(
            session,
            access.user,
            "endpoint.create",
            tenant_id=access.tenant_id,
            target_type="endpoint",
            target_id=row.id,
            data={"name": body.name, "url": row.url},
        )
        out = {"endpoint": endpoint_dict(row)}
        if body.secret is None:
            out["secret"] = secret  # type: ignore[assignment]
        return out


class EndpointPatchIn(BaseModel):
    url: HttpUrl | None = None
    secret: str | None = Field(default=None, min_length=16, max_length=500)
    rotate_secret: bool = False  # generate a new one, shown once
    headers: dict[str, str] | None = None  # replaces all headers
    is_active: bool | None = None


@router.patch("/tenants/{tenant_id}/endpoints/{endpoint_id}")
async def patch_endpoint(
    endpoint_id: uuid.UUID, body: EndpointPatchIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        row = await tenant_row(
            session, Endpoint, endpoint_id, access.tenant_id, "endpoint.not_found"
        )
        changes: dict[str, Any] = {}
        new_secret = None
        if body.url is not None:
            row.url = changes["url"] = str(body.url)
        if body.secret is not None:
            row.secret_enc = services.crypto.encrypt(body.secret)
            changes["secret"] = "set"
        elif body.rotate_secret:
            new_secret = new_token(32)
            row.secret_enc = services.crypto.encrypt(new_secret)
            changes["secret"] = "rotated"
        if body.headers is not None:
            row.headers = body.headers
            changes["header_names"] = sorted(body.headers)
        if body.is_active is not None:
            row.is_active = changes["is_active"] = body.is_active
        await audit(
            session,
            access.user,
            "endpoint.update",
            tenant_id=access.tenant_id,
            target_type="endpoint",
            target_id=row.id,
            data=changes,
        )
        out: dict[str, Any] = {"endpoint": endpoint_dict(row)}
        if new_secret is not None:
            out["secret"] = new_secret
        return out


@router.delete("/tenants/{tenant_id}/endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: uuid.UUID, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Deliveries keep their history (endpoint_id becomes NULL); scenario actions that
    name this endpoint fail loudly until it is created again."""
    async with services.sm() as session, session.begin():
        row = await tenant_row(
            session, Endpoint, endpoint_id, access.tenant_id, "endpoint.not_found"
        )
        await session.delete(row)
        await audit(
            session,
            access.user,
            "endpoint.delete",
            tenant_id=access.tenant_id,
            target_type="endpoint",
            target_id=endpoint_id,
            data={"name": row.name},
        )
        return {"ok": True}


# --- audit log ----------------------------------------------------------------------------


@router.get("/audit")
async def audit_log(
    user: AdminDep,
    services: ServicesDep,
    page: PageDep,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """A tenant's log for its owner; the whole log (tenant_id omitted) for a superadmin."""
    if tenant_id is None:
        user.require_superadmin()
    else:
        user.require(tenant_id, UserRole.OWNER)
    async with services.sm() as session:
        query = select(AuditLog)
        if tenant_id is not None:
            query = query.where(AuditLog.tenant_id == tenant_id)
        rows = (
            await session.execute(
                query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return {
            "items": [
                {
                    "id": str(a.id),
                    "tenant_id": sid(a.tenant_id),
                    "user_id": sid(a.user_id),
                    "user_email": a.user_email,
                    "action": a.action,
                    "target_type": a.target_type,
                    "target_id": a.target_id,
                    "data": a.data or {},
                    "created_at": iso(a.created_at),
                }
                for a in rows
            ]
        }
