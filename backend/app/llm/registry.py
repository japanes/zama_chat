"""Which model serves a role right now. Read from the DB at the start of every call
(one small query), so switching a model in the admin works from the next turn
without a restart. No model names in code."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import ROLES_NEED_JSON_SCHEMA, LlmModel, LlmRole, ModelRole
from app.errors import AppError


@dataclass(frozen=True)
class ModelSpec:
    name: str
    price_input: Decimal
    price_cached_input: Decimal | None
    price_output: Decimal
    supports_json_schema: bool
    supports_reasoning: bool
    supports_vision: bool
    max_output: int | None

    @classmethod
    def from_row(cls, m: LlmModel) -> "ModelSpec":
        return cls(
            name=m.name,
            price_input=m.price_input,
            price_cached_input=m.price_cached_input,
            price_output=m.price_output,
            supports_json_schema=m.supports_json_schema,
            supports_reasoning=m.supports_reasoning,
            supports_vision=m.supports_vision,
            max_output=m.max_output,
        )


@dataclass(frozen=True)
class RoleConfig:
    role: str
    model: ModelSpec
    reasoning_effort: str | None
    timeout_s: float
    fallback: ModelSpec | None
    tenant_override: bool


async def resolve_role(
    session: AsyncSession, role: LlmRole | str, tenant_id: uuid.UUID | None
) -> RoleConfig:
    """Tenant row wins over the shared (tenant_id NULL) row.

    Raises AppError("llm.role_not_assigned") when neither exists, and
    "llm.model_inactive" when the assigned model is switched off.
    """
    role = str(role)
    primary = aliased(LlmModel)
    fallback = aliased(LlmModel)
    tenant_filter = (
        or_(ModelRole.tenant_id == tenant_id, ModelRole.tenant_id.is_(None))
        if tenant_id is not None
        else ModelRole.tenant_id.is_(None)
    )
    rows = (
        await session.execute(
            select(ModelRole, primary, fallback)
            .join(primary, primary.id == ModelRole.model_id)
            .outerjoin(fallback, fallback.id == ModelRole.fallback_model_id)
            .where(ModelRole.role == role, tenant_filter)
            # tenant row first (NULLs last)
            .order_by(ModelRole.tenant_id.asc().nulls_last())
        )
    ).all()
    if not rows:
        raise AppError("llm.role_not_assigned", {"role": role}, status=503)
    assignment, model, fb = rows[0]
    if not model.is_active:
        raise AppError("llm.model_inactive", {"role": role, "model": model.name}, status=503)
    return RoleConfig(
        role=role,
        model=ModelSpec.from_row(model),
        reasoning_effort=assignment.reasoning_effort,
        timeout_s=assignment.timeout_s,
        fallback=ModelSpec.from_row(fb) if fb is not None and fb.is_active else None,
        tenant_override=assignment.tenant_id is not None,
    )


async def get_model(session: AsyncSession, name: str) -> LlmModel:
    model = (
        await session.execute(select(LlmModel).where(LlmModel.name == name))
    ).scalar_one_or_none()
    if model is None:
        raise AppError("models.not_found", {"name": name}, status=404)
    return model


async def roles_using_model(session: AsyncSession, model_id: uuid.UUID) -> list[str]:
    """Roles where the model is primary or fallback — such a model cannot be switched
    off or deleted."""
    rows = (
        await session.execute(
            select(ModelRole.role).where(
                or_(ModelRole.model_id == model_id, ModelRole.fallback_model_id == model_id)
            )
        )
    ).scalars()
    return sorted(set(rows))


async def assign_role(
    session: AsyncSession,
    *,
    role: LlmRole | str,
    model: LlmModel,
    tenant_id: uuid.UUID | None = None,
    reasoning_effort: str | None = None,
    timeout_s: float | None = None,
    fallback: LlmModel | None = None,
) -> ModelRole:
    """Create or update the role assignment, with the checks the admin also relies on."""
    role = LlmRole(role)
    if role in ROLES_NEED_JSON_SCHEMA and not model.supports_json_schema:
        raise AppError("models.json_schema_required", {"role": role.value, "model": model.name})
    if role == LlmRole.VISION and not model.supports_vision:
        raise AppError("models.vision_required", {"role": role.value, "model": model.name})
    if (role == LlmRole.EMBED) != (model.kind == "embedding"):
        raise AppError("models.kind_mismatch", {"role": role.value, "model": model.name})
    if reasoning_effort:
        if not model.supports_reasoning:
            raise AppError("models.reasoning_unsupported", {"model": model.name})
        if model.reasoning_efforts and reasoning_effort not in model.reasoning_efforts:
            raise AppError(
                "models.reasoning_effort_invalid",
                {
                    "model": model.name,
                    "effort": reasoning_effort,
                    "allowed": model.reasoning_efforts,
                },
            )
    if not model.is_active:
        raise AppError("models.inactive", {"model": model.name})
    if fallback is not None and fallback.id == model.id:
        fallback = None

    row = (
        await session.execute(
            select(ModelRole).where(
                ModelRole.role == role.value,
                ModelRole.tenant_id.is_(None)
                if tenant_id is None
                else ModelRole.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ModelRole(tenant_id=tenant_id, role=role.value, model_id=model.id)
        session.add(row)
    row.model_id = model.id
    row.reasoning_effort = reasoning_effort or None
    if timeout_s is not None:
        row.timeout_s = timeout_s
    row.fallback_model_id = fallback.id if fallback is not None else None
    await session.flush()
    return row


def roles_for_model(model: LlmModel) -> list[LlmRole]:
    """Every role the model can serve ('*' in `zama models assign`)."""
    if model.kind == "embedding":
        return [LlmRole.EMBED]
    roles = [LlmRole.REPLY]
    if model.supports_json_schema:
        roles += [LlmRole.REWRITE, LlmRole.EXTRACT, LlmRole.DECIDE]
    if model.supports_vision:
        roles.append(LlmRole.VISION)
    return roles
