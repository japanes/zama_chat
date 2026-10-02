"""LLM models, role assignments and costs.

Models and the shared role assignments are the superadmin's (they cost money for
everyone); a tenant admin may override a role for their tenant and read the list.
Rules the registry enforces (also here): a model on a role cannot be switched off or
deleted, a role that needs JSON output gets only a model that can do it.
"""

import time
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import Date, cast, delete, func, select

from app.admin.access import AdminUser, audit
from app.api.admin.common import get_tenant, iso, money
from app.api.admin.deps import AdminDep, SuperDep
from app.api.deps import ServicesDep
from app.db.models import (
    ROLES_NEED_JSON_SCHEMA,
    Decision,
    LlmCall,
    LlmModel,
    LlmRole,
    ModelRole,
    UserRole,
)
from app.errors import AppError, Conflict, NotFound
from app.llm.openai_client import LLMRequestError, LLMTransportError
from app.llm.pricing import cost_from_response
from app.llm.registry import assign_role, get_model, roles_using_model

router = APIRouter(prefix="/v1/admin", tags=["admin-models"])

TenantQuery = Annotated[uuid.UUID | None, Query()]
CHECK_TIMEOUT_S = 30.0


def _any_admin(user: AdminUser) -> None:
    """Reading models and shared roles: a superadmin or an admin of some tenant."""
    if not user.is_superadmin and not any(
        r in (UserRole.ADMIN, UserRole.OWNER) for r in user.roles.values()
    ):
        user.require_superadmin()


def model_dict(m: LlmModel, roles: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "name": m.name,
        "label": m.label,
        "kind": m.kind,
        "price_input": money(m.price_input),
        "price_cached_input": money(m.price_cached_input),
        "price_output": money(m.price_output),
        "context_window": m.context_window,
        "max_output": m.max_output,
        "supports_json_schema": m.supports_json_schema,
        "supports_reasoning": m.supports_reasoning,
        "supports_vision": m.supports_vision,
        "reasoning_efforts": m.reasoning_efforts or [],
        "is_active": m.is_active,
        "roles": roles or [],
        "updated_at": iso(m.updated_at),
    }


# --- models -------------------------------------------------------------------------------


@router.get("/models")
async def list_models(user: AdminDep, services: ServicesDep) -> dict[str, Any]:
    _any_admin(user)
    async with services.sm() as session:
        models = (await session.execute(select(LlmModel).order_by(LlmModel.name))).scalars()
        rows = (
            await session.execute(
                select(ModelRole.model_id, ModelRole.fallback_model_id, ModelRole.role)
            )
        ).all()
        roles: dict[uuid.UUID, set[str]] = {}
        for r in rows:
            roles.setdefault(r.model_id, set()).add(r.role)
            if r.fallback_model_id:
                roles.setdefault(r.fallback_model_id, set()).add(f"{r.role}:fallback")
        return {"items": [model_dict(m, sorted(roles.get(m.id, set()))) for m in models]}


@router.get("/models/catalog")
async def model_catalog(
    _: SuperDep, services: ServicesDep, kind: str | None = None, q: str | None = None
) -> dict[str, Any]:
    """The Gateway catalog with prices, to pick a model from."""
    models = await services.catalog.list_models(kind)
    if q:
        needle = q.strip().lower()
        models = [m for m in models if needle in m.name.lower() or needle in m.label.lower()]
    async with services.sm() as session:
        added = set((await session.execute(select(LlmModel.name))).scalars())
    return {"items": [{**m.to_dict(), "added": m.name in added} for m in models]}


class ModelFields(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, pattern=r"^(language|embedding)$")
    price_input: Decimal | None = Field(default=None, ge=0)
    price_cached_input: Decimal | None = Field(default=None, ge=0)
    price_output: Decimal | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, ge=1)
    max_output: int | None = Field(default=None, ge=1)
    supports_json_schema: bool | None = None
    supports_reasoning: bool | None = None
    supports_vision: bool | None = None
    reasoning_efforts: list[str] | None = None
    is_active: bool | None = None

    def values(self) -> dict[str, Any]:
        # only what was sent: an explicit null clears a nullable price / limit
        return {k: getattr(self, k) for k in self.model_fields_set}


class ModelCreateIn(ModelFields):
    name: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")
    # prices, limits and capabilities from the Gateway catalog; sent fields win
    from_gateway: bool = True


NOT_NULL = ("price_input", "price_output", "kind", "label", "is_active")


@router.post("/models")
async def add_model(body: ModelCreateIn, user: SuperDep, services: ServicesDep) -> dict[str, Any]:
    data: dict[str, Any] = {"name": body.name, "label": body.name}
    if body.from_gateway:
        data.update((await services.catalog.get(body.name)).model_fields())
    data.update({k: v for k, v in body.values().items() if k not in ("name", "from_gateway")})
    for key in NOT_NULL:
        if key in data and data[key] is None:
            data.pop(key)
    async with services.sm() as session, session.begin():
        exists = (
            await session.execute(select(LlmModel.id).where(LlmModel.name == body.name))
        ).scalar_one_or_none()
        if exists is not None:
            raise Conflict("models.exists", {"name": body.name})
        model = LlmModel(**data)
        session.add(model)
        await session.flush()
        await audit(
            session,
            user,
            "model.add",
            target_type="model",
            target_id=model.name,
            data={"from_gateway": body.from_gateway},
        )
        return {"model": model_dict(model)}


async def _model(session, model_id: uuid.UUID) -> LlmModel:
    model = await session.get(LlmModel, model_id)
    if model is None:
        raise NotFound("models.not_found", {"id": str(model_id)})
    return model


async def _check_assignments(session, model: LlmModel) -> None:
    """After an edit, every role the model is primary on must still accept it."""
    rows = (
        await session.execute(select(ModelRole.role).where(ModelRole.model_id == model.id))
    ).scalars()
    for role in set(rows):
        r = LlmRole(role)
        if r in ROLES_NEED_JSON_SCHEMA and not model.supports_json_schema:
            raise AppError("models.json_schema_required", {"role": role, "model": model.name})
        if r == LlmRole.VISION and not model.supports_vision:
            raise AppError("models.vision_required", {"role": role, "model": model.name})
        if (r == LlmRole.EMBED) != (model.kind == "embedding"):
            raise AppError("models.kind_mismatch", {"role": role, "model": model.name})


@router.patch("/models/{model_id}")
async def patch_model(
    model_id: uuid.UUID, body: ModelFields, user: SuperDep, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session, session.begin():
        model = await _model(session, model_id)
        values = body.values()
        if values.get("is_active") is False:
            roles = await roles_using_model(session, model.id)
            if roles:
                raise Conflict("models.in_use", {"model": model.name, "roles": roles})
        for key, value in values.items():
            if value is None and key in NOT_NULL:
                continue
            setattr(model, key, value)
        await session.flush()
        await _check_assignments(session, model)
        await audit(
            session,
            user,
            "model.update",
            target_type="model",
            target_id=model.name,
            data={k: str(v) if isinstance(v, Decimal) else v for k, v in values.items()},
        )
        return {"model": model_dict(model, await roles_using_model(session, model.id))}


@router.delete("/models/{model_id}")
async def delete_model(
    model_id: uuid.UUID, user: SuperDep, services: ServicesDep
) -> dict[str, Any]:
    """The call log keeps the model name, so old turns still show what answered."""
    async with services.sm() as session, session.begin():
        model = await _model(session, model_id)
        roles = await roles_using_model(session, model.id)
        if roles:
            raise Conflict("models.in_use", {"model": model.name, "roles": roles})
        await session.delete(model)
        await audit(session, user, "model.delete", target_type="model", target_id=model.name)
        return {"ok": True}


class ModelCheckIn(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    kind: str = Field(default="language", pattern=r"^(language|embedding)$")
    reasoning_effort: str | None = Field(default=None, max_length=16)


@router.post("/models/check")
async def check_model(body: ModelCheckIn, _: SuperDep, services: ServicesDep) -> dict[str, Any]:
    """A tiny real call through the Gateway (before or after saving the model):
    {ok, latency_ms, answer | dimensions, cost, error}. Costs a fraction of a cent."""
    backend = services.llm.backend
    started = time.monotonic()
    try:
        if body.kind == "embedding":
            raw = await backend.embed(body.name, ["ping"], timeout=CHECK_TIMEOUT_S)
            vectors = raw.get("data") or []
            dims = len(vectors[0].get("embedding") or []) if vectors else 0
            return {
                "ok": dims > 0,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "dimensions": dims,
                "cost": None,
                "error": None if dims else {"code": "models.check_empty", "params": {}},
            }
        request: dict[str, Any] = {
            "model": body.name,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
        }
        if body.reasoning_effort:
            request["reasoning"] = {"effort": body.reasoning_effort}
        raw = await backend.chat(request, timeout=CHECK_TIMEOUT_S)
    except (LLMTransportError, LLMRequestError) as exc:
        return {
            "ok": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": {
                "code": "models.check_failed",
                "params": {"status": exc.status, "message": str(exc)[:500]},
            },
        }
    choices = raw.get("choices") or []
    answer = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
    usage = raw.get("usage") or {}
    return {
        "ok": bool(answer.strip()),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "answer": answer[:200],
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
        "cost": money(cost_from_response(raw)),
        "error": None if answer.strip() else {"code": "models.check_empty", "params": {}},
    }


# --- roles --------------------------------------------------------------------------------


def _assignment(row: ModelRole, models: dict[uuid.UUID, LlmModel]) -> dict[str, Any]:
    model = models.get(row.model_id)
    fallback = models.get(row.fallback_model_id) if row.fallback_model_id else None
    return {
        "model_id": str(row.model_id),
        "model": model.name if model else None,
        "model_label": model.label if model else None,
        "model_active": bool(model and model.is_active),
        "reasoning_effort": row.reasoning_effort,
        "timeout_s": row.timeout_s,
        "fallback": fallback.name if fallback else None,
        "updated_at": iso(row.updated_at),
    }


@router.get("/roles")
async def list_roles(
    user: AdminDep, services: ServicesDep, tenant_id: TenantQuery = None
) -> dict[str, Any]:
    """Every role: the shared assignment, the tenant's own one and what is used.
    `problem` is set when a turn would fail: no model, or the model is switched off."""
    if tenant_id is None:
        _any_admin(user)
    else:
        user.require(tenant_id, UserRole.ADMIN)
    async with services.sm() as session:
        owner = ModelRole.tenant_id.is_(None)
        if tenant_id is not None:
            owner = owner | (ModelRole.tenant_id == tenant_id)
        rows = list((await session.execute(select(ModelRole).where(owner))).scalars())
        models = {m.id: m for m in (await session.execute(select(LlmModel))).scalars()}
    items = []
    for role in LlmRole:
        shared = next((r for r in rows if r.role == role and r.tenant_id is None), None)
        own = next((r for r in rows if r.role == role and r.tenant_id is not None), None)
        used = own or shared
        problem = None
        if used is None:
            problem = "llm.role_not_assigned"
        elif not (m := models.get(used.model_id)) or not m.is_active:
            problem = "llm.model_inactive"
        items.append(
            {
                "role": role.value,
                "needs_json_schema": role in ROLES_NEED_JSON_SCHEMA,
                "shared": _assignment(shared, models) if shared else None,
                "own": _assignment(own, models) if own else None,
                "effective": "own" if own else ("shared" if shared else None),
                "problem": problem,
            }
        )
    return {"items": items}


class RoleIn(BaseModel):
    tenant_id: uuid.UUID | None = None
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: str | None = Field(default=None, max_length=16)
    timeout_s: float | None = Field(default=None, ge=1, le=600)
    fallback: str | None = Field(default=None, max_length=200)


@router.put("/roles/{role}")
async def set_role(
    role: LlmRole, body: RoleIn, user: AdminDep, services: ServicesDep
) -> dict[str, Any]:
    """Takes effect from the next turn: the role is read at the start of every call."""
    if body.tenant_id is None:
        user.require_superadmin()
    else:
        user.require(body.tenant_id, UserRole.ADMIN)
    async with services.sm() as session, session.begin():
        if body.tenant_id is not None:
            await get_tenant(session, body.tenant_id)
        model = await get_model(session, body.model)
        fallback = await get_model(session, body.fallback) if body.fallback else None
        if fallback is not None and not fallback.is_active:
            raise AppError("models.inactive", {"model": fallback.name})
        row = await assign_role(
            session,
            role=role,
            model=model,
            tenant_id=body.tenant_id,
            reasoning_effort=body.reasoning_effort,
            timeout_s=body.timeout_s,
            fallback=fallback,
        )
        await audit(
            session,
            user,
            "role.assign",
            tenant_id=body.tenant_id,
            target_type="role",
            target_id=role.value,
            data={
                "model": model.name,
                "reasoning_effort": body.reasoning_effort,
                "timeout_s": body.timeout_s,
                "fallback": body.fallback,
            },
        )
        models = {model.id: model}
        if fallback is not None:
            models[fallback.id] = fallback
        return {"role": role.value, "assignment": _assignment(row, models)}


@router.delete("/roles/{role}")
async def reset_role(
    role: LlmRole,
    user: AdminDep,
    services: ServicesDep,
    tenant_id: Annotated[uuid.UUID, Query()],
) -> dict[str, Any]:
    """The tenant goes back to the shared assignment."""
    user.require(tenant_id, UserRole.ADMIN)
    async with services.sm() as session, session.begin():
        await session.execute(
            delete(ModelRole).where(ModelRole.role == role.value, ModelRole.tenant_id == tenant_id)
        )
        await audit(
            session, user, "role.reset", tenant_id=tenant_id, target_type="role", target_id=role
        )
        return {"ok": True}


# --- costs --------------------------------------------------------------------------------


@router.get("/costs")
async def costs(
    user: AdminDep,
    services: ServicesDep,
    tenant_id: TenantQuery = None,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> dict[str, Any]:
    """Spend for the last `days`: LLM calls by model and role, Jev decisions by model,
    and per day. LLM-fallback decisions are counted once, in their LLM call."""
    if tenant_id is None:
        user.require_superadmin()
    else:
        user.require(tenant_id, UserRole.ADMIN)
    now = services.clock.now()
    since = now - timedelta(days=days)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = min(since, month_start)

    llm_scope = [LlmCall.created_at >= start]
    dec_scope = [Decision.created_at >= start, Decision.llm_call_id.is_(None)]
    if tenant_id is not None:
        llm_scope.append(LlmCall.tenant_id == tenant_id)
        dec_scope.append(Decision.tenant_id == tenant_id)

    async with services.sm() as session:
        by_model_role = (
            await session.execute(
                select(
                    LlmCall.model,
                    LlmCall.role,
                    func.count(),
                    func.coalesce(func.sum(LlmCall.tokens_in), 0),
                    func.coalesce(func.sum(LlmCall.tokens_out), 0),
                    func.coalesce(func.sum(LlmCall.cost), 0),
                )
                .where(*llm_scope, LlmCall.created_at >= since)
                .group_by(LlmCall.model, LlmCall.role)
            )
        ).all()
        decisions = (
            await session.execute(
                select(
                    Decision.provider,
                    Decision.model,
                    func.count(),
                    func.count(func.distinct(Decision.batch_id)),
                    func.coalesce(func.sum(Decision.cost), 0),
                )
                .where(*dec_scope, Decision.created_at >= since)
                .group_by(Decision.provider, Decision.model)
            )
        ).all()
        # days are UTC days whatever the DB time zone is
        day = cast(func.timezone("UTC", LlmCall.created_at), Date)
        llm_days = (
            await session.execute(
                select(day, func.coalesce(func.sum(LlmCall.cost), 0))
                .where(*llm_scope)
                .group_by(day)
            )
        ).all()
        dday = cast(func.timezone("UTC", Decision.created_at), Date)
        dec_days = (
            await session.execute(
                select(dday, func.coalesce(func.sum(Decision.cost), 0))
                .where(*dec_scope)
                .group_by(dday)
            )
        ).all()

    per_day: dict[str, Decimal] = {}
    for d, cost in [*llm_days, *dec_days]:
        per_day[d.isoformat()] = per_day.get(d.isoformat(), Decimal(0)) + Decimal(cost)
    today = now.date().isoformat()
    month = sum((v for k, v in per_day.items() if k >= month_start.date().isoformat()), Decimal(0))
    period = sum((v for k, v in per_day.items() if k >= since.date().isoformat()), Decimal(0))
    return {
        "days": days,
        "today": money(per_day.get(today, Decimal(0))),
        "month": money(month),
        "period": money(period),
        "by_model_role": [
            {
                "model": model,
                "role": role,
                "calls": calls,
                "tokens_in": int(tin),
                "tokens_out": int(tout),
                "cost": money(Decimal(cost)),
            }
            for model, role, calls, tin, tout, cost in by_model_role
        ],
        "decisions": [
            {
                "provider": provider,
                "model": model,
                "questions": questions,
                "requests": batches,
                "cost": money(Decimal(cost)),
            }
            for provider, model, questions, batches, cost in decisions
        ],
        "per_day": [
            {"date": d, "cost": money(v)}
            for d, v in sorted(per_day.items())
            if d >= since.date().isoformat()
        ],
    }
