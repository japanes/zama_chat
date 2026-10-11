"""Prompts (versions, rollback), scenarios (check, save as a new version, activate,
disable) and the channel's scenario list (which ones and in what order).

Prompts: `tenant_id` omitted = the shared prompts (superadmin edits them, every tenant
admin may read them); with `tenant_id` = that tenant's own versions, which win over the
shared one. A tenant may only override a key that exists as a shared prompt (the code
asks for known keys only).
"""

import json
import re
import uuid
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from app.admin.access import AdminUser, audit
from app.api.admin.common import get_channel, get_tenant, iso
from app.api.admin.deps import AdminDep, TenantAdmin, TenantOperator
from app.api.deps import ServicesDep
from app.db.models import Channel, ChannelScenario, Prompt, Scenario, UserRole
from app.errors import AppError, Conflict, NotFound
from app.prompts.store import activate_version, save_version
from app.scenarios.draft import DESCRIPTION_LIMIT, draft_scenario
from app.scenarios.loader import parse_yaml, prepare, tenant_languages
from app.scenarios.store import (
    definition_json,
    enable_in_channel,
    is_working_version,
    save_scenario,
)
from app.scenarios.templates import list_templates

router = APIRouter(prefix="/v1/admin", tags=["admin-content"])

PROMPT_KEY = r"^[a-z0-9_]{1,100}$"
SCENARIO_KEY = r"^[a-z0-9_]{1,100}$"


# --- prompts ------------------------------------------------------------------------------


def _prompt_access(user: AdminUser, tenant_id: uuid.UUID | None, *, write: bool) -> None:
    if tenant_id is not None:
        user.require(tenant_id, UserRole.ADMIN)
        return
    if write or not (
        user.is_superadmin
        or any(r in (UserRole.ADMIN, UserRole.OWNER) for r in user.roles.values())
    ):
        user.require_superadmin()


def _owner(tenant_id: uuid.UUID | None):
    return Prompt.tenant_id.is_(None) if tenant_id is None else Prompt.tenant_id == tenant_id


def prompt_dict(p: Prompt, *, body: bool = True) -> dict[str, Any]:
    out = {
        "id": str(p.id),
        "key": p.key,
        "version": p.version,
        "is_active": p.is_active,
        "author": p.author,
        "note": p.note,
        "tenant_id": str(p.tenant_id) if p.tenant_id else None,
        "created_at": iso(p.created_at),
    }
    if body:
        out["body"] = p.body
    return out


TenantQuery = Annotated[uuid.UUID | None, Query()]


@router.get("/prompts")
async def list_prompts(
    user: AdminDep, services: ServicesDep, tenant_id: TenantQuery = None
) -> dict[str, Any]:
    """Every key with its active shared version and (for a tenant) its own one."""
    _prompt_access(user, tenant_id, write=False)
    async with services.sm() as session:
        owner = Prompt.tenant_id.is_(None)
        if tenant_id is not None:
            owner = owner | (Prompt.tenant_id == tenant_id)
        rows = (
            await session.execute(
                select(
                    Prompt.key,
                    Prompt.tenant_id,
                    func.count().label("versions"),
                    func.max(Prompt.version).filter(Prompt.is_active).label("active"),
                    func.max(Prompt.created_at).label("updated_at"),
                )
                .where(owner)
                .group_by(Prompt.key, Prompt.tenant_id)
            )
        ).all()
    items: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = items.setdefault(r.key, {"key": r.key, "shared": None, "own": None})
        scope = {"versions": r.versions, "active": r.active, "updated_at": iso(r.updated_at)}
        item["own" if r.tenant_id is not None else "shared"] = scope
    for item in items.values():
        own_active = item["own"] and item["own"]["active"]
        item["effective"] = "own" if own_active else ("shared" if item["shared"] else None)
    return {"items": sorted(items.values(), key=lambda x: x["key"])}


@router.get("/prompts/{key}/versions")
async def prompt_versions(
    key: str, user: AdminDep, services: ServicesDep, tenant_id: TenantQuery = None
) -> dict[str, Any]:
    _prompt_access(user, tenant_id, write=False)
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(Prompt)
                .where(Prompt.key == key, _owner(tenant_id))
                .order_by(Prompt.version.desc())
            )
        ).scalars()
        out: dict[str, Any] = {"key": key, "items": [prompt_dict(p) for p in rows]}
        if tenant_id is not None:
            # what the tenant falls back to (and what a diff compares against)
            shared = (
                await session.execute(
                    select(Prompt).where(
                        Prompt.key == key, Prompt.tenant_id.is_(None), Prompt.is_active
                    )
                )
            ).scalar_one_or_none()
            out["shared_active"] = prompt_dict(shared) if shared else None
    if not out["items"] and not out.get("shared_active"):
        raise NotFound("prompt.not_found", {"key": key})
    return out


class PromptVersionIn(BaseModel):
    tenant_id: uuid.UUID | None = None
    body: str = Field(min_length=1, max_length=200_000)
    note: str | None = Field(default=None, max_length=2000)
    activate: bool = True


def _check_body(key: str, body: str) -> None:
    """JSON prompts (the legacy ones) must stay valid JSON."""
    if body.lstrip().startswith("{"):
        try:
            json.loads(body)
        except ValueError as exc:
            raise AppError("prompt.invalid_json", {"key": key, "error": str(exc)[:200]}) from exc


@router.post("/prompts/{key}/versions")
async def new_prompt_version(
    key: str, body: PromptVersionIn, user: AdminDep, services: ServicesDep
) -> dict[str, Any]:
    _prompt_access(user, body.tenant_id, write=True)
    if not re.match(PROMPT_KEY, key):
        raise AppError("prompt.key_invalid", {"key": key})
    _check_body(key, body.body)
    async with services.sm() as session, session.begin():
        if body.tenant_id is not None:
            await get_tenant(session, body.tenant_id)
            shared = (
                await session.execute(
                    select(Prompt.id).where(Prompt.key == key, Prompt.tenant_id.is_(None)).limit(1)
                )
            ).scalar_one_or_none()
            if shared is None:
                raise AppError("prompt.unknown_key", {"key": key})
        # one writer per key and owner: versions are counted under a lock
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(f"prompt:{body.tenant_id}:{key}")))
        )
        prompt = await save_version(
            session,
            key=key,
            body=body.body,
            tenant_id=body.tenant_id,
            author=user.email,
            note=body.note,
            activate=body.activate,
        )
        await audit(
            session,
            user,
            "prompt.version",
            tenant_id=body.tenant_id,
            target_type="prompt",
            target_id=key,
            data={"version": prompt.version, "activate": body.activate},
        )
        return {"prompt": prompt_dict(prompt)}


class ActivateIn(BaseModel):
    tenant_id: uuid.UUID | None = None
    version: int = Field(ge=1)


@router.post("/prompts/{key}/activate")
async def activate_prompt(
    key: str, body: ActivateIn, user: AdminDep, services: ServicesDep
) -> dict[str, Any]:
    """Roll back / forward to an existing version."""
    _prompt_access(user, body.tenant_id, write=True)
    async with services.sm() as session, session.begin():
        prompt = await activate_version(
            session, key=key, version=body.version, tenant_id=body.tenant_id
        )
        await audit(
            session,
            user,
            "prompt.activate",
            tenant_id=body.tenant_id,
            target_type="prompt",
            target_id=key,
            data={"version": body.version},
        )
        return {"prompt": prompt_dict(prompt)}


@router.delete("/prompts/{key}/override")
async def reset_prompt_override(
    key: str, user: AdminDep, services: ServicesDep, tenant_id: Annotated[uuid.UUID, Query()]
) -> dict[str, Any]:
    """The tenant goes back to the shared prompt; its versions stay for history."""
    _prompt_access(user, tenant_id, write=True)
    async with services.sm() as session, session.begin():
        await session.execute(
            update(Prompt)
            .where(Prompt.key == key, Prompt.tenant_id == tenant_id, Prompt.is_active)
            .values(is_active=False)
        )
        await audit(
            session, user, "prompt.reset", tenant_id=tenant_id, target_type="prompt", target_id=key
        )
        return {"ok": True}


# --- scenarios ----------------------------------------------------------------------------


class ScenarioSource(BaseModel):
    """The scenario as a JSON object (the form editor) or YAML text (the text editor)."""

    definition: dict[str, Any] | None = None
    yaml: str | None = Field(default=None, max_length=500_000)

    def data(self) -> Any:
        if self.definition is not None:
            return self.definition
        if self.yaml is None:
            raise AppError("scenario.source_missing")
        try:
            return parse_yaml(self.yaml)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            raise AppError(
                "scenario.yaml_invalid",
                {
                    "error": str(getattr(exc, "problem", None) or exc)[:300],
                    "line": mark.line + 1 if mark is not None else None,
                },
            ) from exc


def scenario_row_dict(s: Scenario, *, definition: bool = False) -> dict[str, Any]:
    out = {
        "id": str(s.id),
        "key": s.key,
        "version": s.version,
        "title": s.title,
        "is_active": s.is_active,
        "author": s.author,
        "note": s.note,
        "created_at": iso(s.created_at),
    }
    if definition:
        out["definition"] = s.definition
    return out


@router.get("/tenants/{tenant_id}/scenarios")
async def list_scenarios(access: TenantOperator, services: ServicesDep) -> dict[str, Any]:
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(
                    Scenario.key,
                    Scenario.version,
                    Scenario.title,
                    Scenario.is_active,
                    Scenario.created_at,
                )
                .where(Scenario.tenant_id == access.tenant_id)
                .order_by(Scenario.key, Scenario.version)
            )
        ).all()
        channels = (
            await session.execute(
                select(ChannelScenario, Channel.title)
                .join(Channel, Channel.id == ChannelScenario.channel_id)
                .where(ChannelScenario.tenant_id == access.tenant_id)
            )
        ).all()
    items: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = items.setdefault(
            r.key, {"key": r.key, "versions": 0, "active_version": None, "channels": []}
        )
        item["versions"] += 1
        item["latest_version"] = r.version
        item["title"] = r.title if item["active_version"] is None else item["title"]
        item["updated_at"] = iso(r.created_at)
        if r.is_active:
            item["active_version"] = r.version
            item["title"] = r.title
    for cs, title in channels:
        if cs.scenario_key in items:
            items[cs.scenario_key]["channels"].append(
                {
                    "channel_id": str(cs.channel_id),
                    "title": title,
                    "position": cs.position,
                    "is_enabled": cs.is_enabled,
                }
            )
    return {"items": sorted(items.values(), key=lambda x: x["key"])}


async def _versions(session, tenant_id: uuid.UUID, key: str) -> list[Scenario]:
    return list(
        (
            await session.execute(
                select(Scenario)
                .where(Scenario.tenant_id == tenant_id, Scenario.key == key)
                .order_by(Scenario.version.desc())
            )
        ).scalars()
    )


@router.get("/tenants/{tenant_id}/scenarios/{key}")
async def get_scenario(key: str, access: TenantOperator, services: ServicesDep) -> dict[str, Any]:
    """The active version (or the latest when the scenario is switched off) with its
    warnings, and the list of versions."""
    async with services.sm() as session:
        tenant = await get_tenant(session, access.tenant_id)
        versions = await _versions(session, access.tenant_id, key)
    if not versions:
        raise NotFound("scenario.not_found", {"key": key})
    current = next((v for v in versions if v.is_active), versions[0])
    try:
        _, warnings = prepare(current.definition, tenant_languages(tenant))
    except AppError as exc:
        # saved before a rule got stricter: shown, so it can be fixed
        warnings = [exc.to_dict()]
    return {
        "key": key,
        "current": scenario_row_dict(current, definition=True),
        "warnings": warnings,
        "languages": tenant_languages(tenant),
        "versions": [scenario_row_dict(v) for v in versions],
    }


@router.get("/tenants/{tenant_id}/scenarios/{key}/versions/{version}")
async def get_scenario_version(
    key: str, version: int, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session:
        row = (
            await session.execute(
                select(Scenario).where(
                    Scenario.tenant_id == access.tenant_id,
                    Scenario.key == key,
                    Scenario.version == version,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFound("scenario.version_not_found", {"key": key, "version": version})
    return {"scenario": scenario_row_dict(row, definition=True)}


@router.get("/tenants/{tenant_id}/scenario-templates")
async def scenario_templates(access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    """Ready scenarios to start a new one from. A template becomes a draft in the editor
    and is saved as the tenant's own scenario; nothing is saved here."""
    return {"items": list_templates(services.settings.scenario_templates_dir)}


class ScenarioDraftIn(BaseModel):
    # what the bot must do, in the person's own words
    description: str = Field(min_length=1, max_length=DESCRIPTION_LIMIT)


@router.post("/tenants/{tenant_id}/scenarios/draft")
async def draft_scenario_route(
    body: ScenarioDraftIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """A draft from a description in words: {definition, warnings, model}. Not saved —
    the editor opens it, the person fixes and saves it."""
    async with services.sm() as session:
        tenant = await get_tenant(session, access.tenant_id)
    defn, warnings, model = await draft_scenario(
        services, access.tenant_id, tenant_languages(tenant), body.description.strip()
    )
    return {"definition": definition_json(defn), "warnings": warnings, "model": model}


@router.post("/tenants/{tenant_id}/scenarios/check")
async def check_scenario(
    body: ScenarioSource, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Check without saving: {valid, error, warnings, definition (normalized), unchanged
    (the working version is the same: saving would change nothing)}."""
    async with services.sm() as session:
        tenant = await get_tenant(session, access.tenant_id)
        try:
            defn, warnings = prepare(body.data(), tenant_languages(tenant))
        except AppError as exc:
            return {"valid": False, "error": exc.to_dict(), "warnings": [], "definition": None}
        unchanged = await is_working_version(session, access.tenant_id, defn)
    return {
        "valid": True,
        "error": None,
        "warnings": warnings,
        "key": defn.key,
        "definition": definition_json(defn),
        "unchanged": unchanged,
    }


class ScenarioSaveIn(ScenarioSource):
    note: str | None = Field(default=None, max_length=2000)
    activate: bool = True
    # switch the scenario on in these channels (added to the end of their lists)
    enable_in_channels: list[uuid.UUID] = Field(default_factory=list)


@router.post("/tenants/{tenant_id}/scenarios")
async def save_scenario_route(
    body: ScenarioSaveIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """A new version when the definition differs from the active one."""
    async with services.sm() as session, session.begin():
        tenant = await get_tenant(session, access.tenant_id)
        defn, warnings = prepare(body.data(), tenant_languages(tenant))
        row, created = await save_scenario(
            session,
            access.tenant_id,
            defn,
            author=access.user.email,
            note=body.note,
            activate=body.activate,
        )
        for channel_id in body.enable_in_channels:
            await get_channel(session, channel_id, access.tenant_id)
            await enable_in_channel(
                session, tenant_id=access.tenant_id, channel_id=channel_id, keys=[defn.key]
            )
        if created:
            await audit(
                session,
                access.user,
                "scenario.version",
                tenant_id=access.tenant_id,
                target_type="scenario",
                target_id=defn.key,
                data={"version": row.version, "activate": body.activate},
            )
        return {"scenario": scenario_row_dict(row), "created": created, "warnings": warnings}


class ScenarioActivateIn(BaseModel):
    version: int = Field(ge=1)


@router.post("/tenants/{tenant_id}/scenarios/{key}/activate")
async def activate_scenario(
    key: str, body: ScenarioActivateIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Roll back / forward. Open runs keep the version they started with."""
    async with services.sm() as session, session.begin():
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(f"scenario:{access.tenant_id}:{key}")))
        )
        row = (
            await session.execute(
                select(Scenario).where(
                    Scenario.tenant_id == access.tenant_id,
                    Scenario.key == key,
                    Scenario.version == body.version,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("scenario.version_not_found", {"key": key, "version": body.version})
        try:
            prepare(row.definition, tenant_languages(await get_tenant(session, access.tenant_id)))
        except AppError as exc:
            raise Conflict(
                "scenario.version_invalid", {"version": body.version, **exc.to_dict()}
            ) from exc
        await session.execute(
            update(Scenario)
            .where(Scenario.tenant_id == access.tenant_id, Scenario.key == key, Scenario.is_active)
            .values(is_active=False)
        )
        await session.flush()
        row.is_active = True
        await audit(
            session,
            access.user,
            "scenario.activate",
            tenant_id=access.tenant_id,
            target_type="scenario",
            target_id=key,
            data={"version": body.version},
        )
        return {"scenario": scenario_row_dict(row)}


@router.post("/tenants/{tenant_id}/scenarios/{key}/disable")
async def disable_scenario(key: str, access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    """No new runs start; open runs go on with their version."""
    async with services.sm() as session, session.begin():
        result = await session.execute(
            update(Scenario)
            .where(Scenario.tenant_id == access.tenant_id, Scenario.key == key, Scenario.is_active)
            .values(is_active=False)
        )
        if not result.rowcount:  # type: ignore[attr-defined]
            raise NotFound("scenario.not_active", {"key": key})
        await audit(
            session,
            access.user,
            "scenario.disable",
            tenant_id=access.tenant_id,
            target_type="scenario",
            target_id=key,
        )
        return {"ok": True}


# --- channel scenarios --------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/channels/{channel_id}/scenarios")
async def channel_scenarios(
    channel_id: uuid.UUID, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    """The channel's list in order; `available` = the tenant's scenarios not in it."""
    async with services.sm() as session:
        await get_channel(session, channel_id, access.tenant_id)
        listed = list(
            (
                await session.execute(
                    select(ChannelScenario)
                    .where(ChannelScenario.channel_id == channel_id)
                    .order_by(ChannelScenario.position, ChannelScenario.scenario_key)
                )
            ).scalars()
        )
        rows = (
            await session.execute(
                select(Scenario.key, Scenario.version, Scenario.title, Scenario.is_active)
                .where(Scenario.tenant_id == access.tenant_id)
                .order_by(Scenario.key, Scenario.version)
            )
        ).all()
    info: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = info.setdefault(r.key, {"title": r.title, "active_version": None})
        if r.is_active:
            item.update(title=r.title, active_version=r.version)
    keys = {cs.scenario_key for cs in listed}
    return {
        "items": [
            {
                "key": cs.scenario_key,
                "position": cs.position,
                "is_enabled": cs.is_enabled,
                **info.get(cs.scenario_key, {"title": None, "active_version": None}),
            }
            for cs in listed
        ],
        "available": [{"key": k, **v} for k, v in sorted(info.items()) if k not in keys],
    }


class ChannelScenarioItem(BaseModel):
    key: str = Field(pattern=SCENARIO_KEY)
    is_enabled: bool = True


class ChannelScenariosIn(BaseModel):
    # the whole list in order: the first one wins focus on equal start votes
    items: list[ChannelScenarioItem] = Field(max_length=200)


@router.put("/tenants/{tenant_id}/channels/{channel_id}/scenarios")
async def set_channel_scenarios(
    channel_id: uuid.UUID, body: ChannelScenariosIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    keys = [i.key for i in body.items]
    if len(set(keys)) != len(keys):
        raise AppError(
            "channel.scenario_duplicate", {"keys": sorted({k for k in keys if keys.count(k) > 1})}
        )
    async with services.sm() as session, session.begin():
        await get_channel(session, channel_id, access.tenant_id)
        known = set(
            (
                await session.execute(
                    select(Scenario.key)
                    .where(Scenario.tenant_id == access.tenant_id, Scenario.key.in_(keys))
                    .distinct()
                )
            ).scalars()
        )
        missing = [k for k in keys if k not in known]
        if missing:
            raise NotFound("scenario.not_found", {"keys": missing})
        await session.execute(
            delete(ChannelScenario).where(ChannelScenario.channel_id == channel_id)
        )
        for position, item in enumerate(body.items):
            session.add(
                ChannelScenario(
                    tenant_id=access.tenant_id,
                    channel_id=channel_id,
                    scenario_key=item.key,
                    position=position,
                    is_enabled=item.is_enabled,
                )
            )
        await audit(
            session,
            access.user,
            "channel.scenarios",
            tenant_id=access.tenant_id,
            target_type="channel",
            target_id=channel_id,
            data={"items": [i.model_dump() for i in body.items]},
        )
    return await channel_scenarios(channel_id, access, services)
