"""Decisions: how Jev and the LLM answer each question, and the rules that choose between
them (decisions/policy.py).

Questions are grouped by key; numbered keys (`cand:12`, `kb:3`) fall into one group
(`cand:*`). For every group: how many times it was asked, how often Jev's answer was used,
how many went to the LLM (unsure / Jev failed), how many came back unsure, the Jev
confidence spread and how often Jev and the LLM agree at each confidence — the LLM answers
come from shadow mode and from the fallback. The page picks a threshold from that.

Rules: the tenant admin edits the tenant's ones; the shared ones are the superadmin's.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.orm import aliased

from app.admin.access import AdminUser, audit
from app.api.admin.common import get_tenant, iso, money, sid
from app.api.admin.deps import AdminDep, SuperDep, TenantAdmin
from app.api.deps import ServicesDep
from app.db.models import (
    BelowAction,
    Decision,
    DecisionProvider,
    DecisionReason,
    DecisionRule,
    PolicyMode,
    Turn,
    UserRole,
)
from app.decisions.policy import (
    DEFAULT_THRESHOLD,
    PATTERN_RE,
    Rule,
    policy_from,
    rule_rows,
)
from app.errors import AppError

router = APIRouter(prefix="/v1/admin", tags=["admin-decisions"])

BUCKETS = 20  # confidence steps of 0.05
MAX_RULES = 200
Days = Annotated[int, Query(ge=1, le=90)]

# `cand:12` → `cand:*`: one group for the numbered candidate questions
GROUP = func.regexp_replace(Decision.key, ":[0-9]+$", ":*")


def _bucket(column: Any) -> Any:
    """1..BUCKETS; confidence 1.0 goes to the last one."""
    return func.least(func.width_bucket(column, 0.0, 1.0, BUCKETS), BUCKETS)


def _count(*cond: Any) -> Any:
    return func.count().filter(and_(*cond))


# a question is counted once: its Jev row, or the LLM row when the LLM answered alone
ASKED = (Decision.provider != DecisionProvider.LLM) | (Decision.reason == DecisionReason.PRIMARY)
IS_JEV = Decision.provider == DecisionProvider.JEV
IS_LLM = Decision.provider == DecisionProvider.LLM


def _rule_dict(row: DecisionRule) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "pattern": row.pattern,
        "mode": row.mode,
        "threshold": row.threshold,
        "below": row.below,
        "note": row.note,
        "updated_at": iso(row.updated_at),
    }


DEFAULT_RULE = Rule().to_dict() | {"pattern": None}


# --- stats --------------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/decisions/stats")
async def decision_stats(access: TenantAdmin, services: ServicesDep, days: Days = 7) -> dict:
    since = services.clock.now() - timedelta(days=days)
    scope = [Decision.tenant_id == access.tenant_id, Decision.created_at >= since]
    j = aliased(Decision)
    m = aliased(Decision)
    j_group = func.regexp_replace(j.key, ":[0-9]+$", ":*")
    async with services.sm() as session:
        groups = (
            await session.execute(
                select(
                    GROUP.label("group"),
                    func.max(Decision.key).label("sample"),
                    _count(ASKED).label("asked"),
                    _count(IS_JEV).label("jev"),
                    _count(IS_JEV, Decision.used).label("jev_used"),
                    _count(IS_JEV, Decision.error.is_not(None)).label("jev_failed"),
                    _count(IS_LLM, Decision.reason == DecisionReason.BELOW_THRESHOLD).label(
                        "below_threshold"
                    ),
                    _count(IS_LLM, Decision.reason == DecisionReason.JEV_ERROR).label("jev_error"),
                    _count(Decision.reason == DecisionReason.UNSURE).label("unsure"),
                    _count(IS_LLM, Decision.reason == DecisionReason.PRIMARY).label("llm_only"),
                    _count(IS_LLM, Decision.reason == DecisionReason.SHADOW).label("shadow"),
                    func.avg(case((IS_JEV, Decision.confidence))).label("jev_confidence"),
                    func.avg(
                        case((IS_JEV & (Decision.error.is_(None)), Decision.latency_ms))
                    ).label("jev_latency_ms"),
                    func.coalesce(func.sum(case((IS_JEV, Decision.cost))), 0).label("jev_cost"),
                    func.coalesce(func.sum(case((IS_LLM, Decision.cost))), 0).label("llm_cost"),
                    func.max(Decision.created_at).label("last_at"),
                )
                .where(*scope)
                .group_by(GROUP)
                .order_by(GROUP)
            )
        ).all()
        spread = (
            await session.execute(
                select(GROUP, _bucket(Decision.confidence), func.count())
                .where(*scope, IS_JEV, Decision.confidence.is_not(None))
                .group_by(GROUP, _bucket(Decision.confidence))
            )
        ).all()
        # Jev and LLM answers to the same question in the same turn
        pairs = (
            await session.execute(
                select(
                    j_group,
                    _bucket(j.confidence),
                    func.count(),
                    func.count().filter(j.answer["value"] == m.answer["value"]),
                )
                .select_from(j)
                .join(
                    m,
                    and_(
                        m.turn_id == j.turn_id,
                        m.key == j.key,
                        m.provider == DecisionProvider.LLM,
                        m.answer.is_not(None),
                    ),
                )
                .where(
                    j.tenant_id == access.tenant_id,
                    j.created_at >= since,
                    j.provider == DecisionProvider.JEV,
                    j.turn_id.is_not(None),
                    j.answer.is_not(None),
                    j.confidence.is_not(None),
                )
                .group_by(j_group, _bucket(j.confidence))
            )
        ).all()
        rows = await rule_rows(session, access.tenant_id)
    policy = policy_from(rows)
    owners = {r.pattern: r.tenant_id for r in sorted(rows, key=lambda r: r.tenant_id is not None)}

    buckets: dict[str, list[dict[str, Any]]] = {}
    for group, _sample, *_ in groups:
        buckets[group] = [
            {"from": round((n - 1) / BUCKETS, 2), "jev": 0, "pairs": 0, "agree": 0}
            for n in range(1, BUCKETS + 1)
        ]
    for group, bucket, count in spread:
        if group in buckets and bucket:
            buckets[group][int(bucket) - 1]["jev"] = count
    for group, bucket, count, agree in pairs:
        if group in buckets and bucket:
            buckets[group][int(bucket) - 1]["pairs"] = count
            buckets[group][int(bucket) - 1]["agree"] = agree

    items = []
    totals: dict[str, Any] = {
        "asked": 0,
        "jev": 0,
        "jev_used": 0,
        "below_threshold": 0,
        "jev_error": 0,
        "unsure": 0,
        "shadow": 0,
        "pairs": 0,
        "agree": 0,
        "cost": Decimal(0),
    }
    for g in groups:
        pattern = policy.pattern_for(g.sample)
        rule = policy.rule(g.sample)
        pair_count = sum(b["pairs"] for b in buckets[g.group])
        agree = sum(b["agree"] for b in buckets[g.group])
        cost = Decimal(g.jev_cost) + Decimal(g.llm_cost)
        items.append(
            {
                "group": g.group,
                "sample": g.sample,
                "asked": g.asked,
                "jev": g.jev,
                "jev_used": g.jev_used,
                "jev_failed": g.jev_failed,
                "below_threshold": g.below_threshold,
                "jev_error": g.jev_error,
                "unsure": g.unsure,
                "llm_only": g.llm_only,
                "shadow": g.shadow,
                "jev_confidence": round(g.jev_confidence, 4)
                if g.jev_confidence is not None
                else None,
                "jev_latency_ms": round(g.jev_latency_ms) if g.jev_latency_ms is not None else None,
                "jev_cost": money(Decimal(g.jev_cost)),
                "llm_cost": money(Decimal(g.llm_cost)),
                "pairs": pair_count,
                "agree": agree,
                "last_at": iso(g.last_at),
                "buckets": buckets[g.group],
                "rule": rule.to_dict() | {"pattern": pattern},
                "rule_source": (
                    None
                    if pattern is None
                    else ("own" if owners.get(pattern) is not None else "shared")
                ),
            }
        )
        for name in (
            "asked",
            "jev",
            "jev_used",
            "below_threshold",
            "jev_error",
            "unsure",
            "shadow",
        ):
            totals[name] += getattr(g, name)
        totals["pairs"] += pair_count
        totals["agree"] += agree
        totals["cost"] += cost
    totals["cost"] = money(totals["cost"])
    return {
        "days": days,
        "since": iso(since),
        "bucket_size": round(1 / BUCKETS, 4),
        "default_rule": DEFAULT_RULE,
        "totals": totals,
        "items": items,
    }


@router.get("/tenants/{tenant_id}/decisions/disagreements")
async def disagreements(
    access: TenantAdmin,
    services: ServicesDep,
    key: Annotated[str, Query(min_length=1, max_length=200)],
    days: Days = 7,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    agree: bool = False,
) -> dict[str, Any]:
    """Questions of one group where Jev and the LLM answered differently (`agree=true`:
    the same), newest first, with the rewritten text the turn decided on."""
    since = services.clock.now() - timedelta(days=days)
    j = aliased(Decision)
    m = aliased(Decision)
    j_group = func.regexp_replace(j.key, ":[0-9]+$", ":*")
    same = j.answer["value"] == m.answer["value"]
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(j, m, Turn.rewritten, Turn.input_text, Turn.conversation_id)
                .join(
                    m,
                    and_(
                        m.turn_id == j.turn_id,
                        m.key == j.key,
                        m.provider == DecisionProvider.LLM,
                        m.answer.is_not(None),
                    ),
                )
                .join(Turn, Turn.id == j.turn_id)
                .where(
                    j.tenant_id == access.tenant_id,
                    j.created_at >= since,
                    j.provider == DecisionProvider.JEV,
                    j.answer.is_not(None),
                    j_group == key,
                    same if agree else ~same,
                )
                .order_by(j.created_at.desc())
                .limit(limit)
            )
        ).all()
    items = []
    for jev, llm, rewritten, input_text, conversation_id in rows:
        items.append(
            {
                "key": jev.key,
                "turn_id": sid(jev.turn_id),
                "conversation_id": sid(conversation_id),
                "created_at": iso(jev.created_at),
                "rewritten": rewritten,
                "input_text": input_text,
                "instructions": (jev.question or {}).get("instructions"),
                "criteria": (jev.question or {}).get("criteria"),
                "jev": _side(jev),
                "llm": _side(llm),
            }
        )
    return {"items": items}


def _side(row: Decision) -> dict[str, Any]:
    return {
        "value": (row.answer or {}).get("value"),
        "confidence": round(row.confidence, 4) if row.confidence is not None else None,
        "model": row.model,
        "reason": row.reason,
        "used": row.used,
    }


# --- rules --------------------------------------------------------------------------------


class RuleIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    mode: PolicyMode = PolicyMode.JEV
    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0, le=1)
    below: BelowAction = BelowAction.LLM
    note: str | None = Field(default=None, max_length=500)


class RulesIn(BaseModel):
    rules: list[RuleIn] = Field(default_factory=list, max_length=MAX_RULES)


def _check_rules(rules: list[RuleIn]) -> None:
    seen: set[str] = set()
    for rule in rules:
        pattern = rule.pattern.strip()
        if not PATTERN_RE.match(pattern):
            raise AppError("decision_policy.pattern_invalid", {"pattern": rule.pattern})
        if pattern in seen:
            raise AppError("decision_policy.pattern_duplicate", {"pattern": pattern})
        seen.add(pattern)


async def _replace_rules(
    services: Any, user: AdminUser, tenant_id: uuid.UUID | None, body: RulesIn
) -> list[DecisionRule]:
    _check_rules(body.rules)
    async with services.sm() as session, session.begin():
        if tenant_id is not None:
            await get_tenant(session, tenant_id)
        owner = (
            DecisionRule.tenant_id.is_(None)
            if tenant_id is None
            else DecisionRule.tenant_id == tenant_id
        )
        await session.execute(delete(DecisionRule).where(owner))
        rows = [
            DecisionRule(
                tenant_id=tenant_id,
                pattern=r.pattern.strip(),
                mode=r.mode,
                threshold=r.threshold,
                below=r.below,
                note=(r.note or "").strip() or None,
            )
            for r in body.rules
        ]
        session.add_all(rows)
        await session.flush()
        for row in rows:
            await session.refresh(row)
        await audit(
            session,
            user,
            "decision_policy.update",
            tenant_id=tenant_id,
            target_type="decision_policy",
            target_id=tenant_id or "shared",
            data={"rules": [Rule(r.pattern, r.mode, r.threshold, r.below).to_dict() for r in rows]},
        )
        return sorted(rows, key=lambda r: r.pattern)


@router.get("/tenants/{tenant_id}/decisions/policy")
async def tenant_policy(access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    """The tenant's rules and the shared ones under them (read-only here)."""
    async with services.sm() as session:
        rows = await rule_rows(session, access.tenant_id)
    return {
        "own": [_rule_dict(r) for r in rows if r.tenant_id is not None],
        "shared": [_rule_dict(r) for r in rows if r.tenant_id is None],
        "default_rule": DEFAULT_RULE,
        "can_edit_shared": access.user.is_superadmin,
    }


@router.put("/tenants/{tenant_id}/decisions/policy")
async def set_tenant_policy(
    body: RulesIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """Replaces the tenant's rules; works from the next turn."""
    rows = await _replace_rules(services, access.user, access.tenant_id, body)
    return {"own": [_rule_dict(r) for r in rows]}


@router.get("/decisions/policy")
async def shared_policy(user: AdminDep, services: ServicesDep) -> dict[str, Any]:
    if not user.is_superadmin and not any(
        r in (UserRole.ADMIN, UserRole.OWNER) for r in user.roles.values()
    ):
        user.require_superadmin()
    async with services.sm() as session:
        rows = await rule_rows(session, None)
    return {"shared": [_rule_dict(r) for r in rows], "default_rule": DEFAULT_RULE}


@router.put("/decisions/policy")
async def set_shared_policy(body: RulesIn, user: SuperDep, services: ServicesDep) -> dict:
    rows = await _replace_rules(services, user, None, body)
    return {"shared": [_rule_dict(r) for r in rows]}
