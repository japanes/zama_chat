"""Confidence threshold and rules by question key."""

import uuid

import pytest

from app.db.models import BelowAction, DecisionRule, PolicyMode
from app.decisions.policy import (
    DEFAULT_THRESHOLD,
    PATTERN_RE,
    DecisionPolicy,
    Rule,
    policy_from,
)


def test_default_threshold():
    policy = DecisionPolicy()
    assert policy.threshold("wants_human") == DEFAULT_THRESHOLD
    assert policy.is_sure("wants_human", DEFAULT_THRESHOLD)
    assert not policy.is_sure("wants_human", DEFAULT_THRESHOLD - 0.01)


def test_exact_key_wins_over_pattern():
    policy = DecisionPolicy(
        default_threshold=0.6,
        thresholds={"start:*": 0.8, "start:booking.wants_booking": 0.9},
    )
    assert policy.threshold("start:booking.wants_booking") == 0.9
    assert policy.threshold("start:booking.service") == 0.8
    assert policy.threshold("service_type") == 0.6


def test_longest_pattern_wins():
    policy = DecisionPolicy(thresholds={"field:*": 0.75, "field:interview.*": 0.85})
    assert policy.threshold("field:interview.niche") == 0.85
    assert policy.threshold("field:booking.date") == 0.75


# --- rules (stage 7) ---------------------------------------------------------------------


def test_no_rule_is_jev_with_default_threshold_and_the_careful_reading_below():
    rule = DecisionPolicy().rule("wants_human")
    assert rule == Rule(threshold=DEFAULT_THRESHOLD)
    assert (rule.mode, rule.below) == (PolicyMode.JEV, BelowAction.SAFE)


def test_rules_exact_key_then_longest_pattern():
    policy = DecisionPolicy(
        rules={
            "*": Rule("*", PolicyMode.JEV, 0.6),
            "start:*": Rule("start:*", PolicyMode.SHADOW, 0.8),
            "start:booking.*": Rule("start:booking.*", PolicyMode.JEV, 0.9, BelowAction.KEEP),
            "start:booking.wants_booking": Rule("start:booking.wants_booking", PolicyMode.LLM, 0.5),
        }
    )
    assert policy.pattern_for("start:booking.wants_booking") == "start:booking.wants_booking"
    assert policy.rule("start:booking.wants_booking").mode == PolicyMode.LLM
    assert policy.pattern_for("start:booking.service") == "start:booking.*"
    assert policy.rule("start:booking.service").below == BelowAction.KEEP
    assert policy.threshold("start:interview.x") == 0.8
    assert policy.pattern_for("service_type") == "*"
    assert policy.threshold("service_type") == 0.6


def test_rules_win_over_old_thresholds():
    policy = DecisionPolicy(
        thresholds={"field:*": 0.75},
        rules={"field:interview.*": Rule("field:interview.*", threshold=0.95)},
    )
    assert policy.threshold("field:interview.niche") == 0.95
    # no rule matches → the stage 1 thresholds still work
    assert policy.pattern_for("field:booking.date") is None
    assert policy.threshold("field:booking.date") == 0.75


def test_force_mode_changes_only_the_mode():
    policy = DecisionPolicy(
        rules={"a": Rule("a", PolicyMode.JEV, 0.9, BelowAction.OPERATOR)}
    ).with_mode(PolicyMode.SHADOW)
    assert policy.rule("a") == Rule("a", PolicyMode.SHADOW, 0.9, BelowAction.OPERATOR)
    assert policy.rule("b").mode == PolicyMode.SHADOW
    base = DecisionPolicy()
    assert base.with_mode(None) is base


def _row(pattern: str, tenant_id: uuid.UUID | None, threshold: float) -> DecisionRule:
    return DecisionRule(
        tenant_id=tenant_id,
        pattern=pattern,
        mode=PolicyMode.JEV,
        threshold=threshold,
        below=BelowAction.LLM,
    )


def test_tenant_rule_replaces_shared_rule_with_the_same_pattern():
    tenant = uuid.uuid4()
    rows = [  # tenant row first: the order of the rows must not matter
        _row("start:*", tenant, 0.95),
        _row("start:*", None, 0.8),
        _row("*", None, 0.6),
    ]
    policy = policy_from(rows)
    assert policy.threshold("start:x.y") == 0.95
    assert policy.threshold("wants_human") == 0.6


@pytest.mark.parametrize(
    ("pattern", "ok"),
    [
        ("start:*", True),
        ("filter:booking.name.full_name", True),
        ("cand:[0-9]?", True),
        ("field:a b", False),
        ("", False),
        ("x" * 201, False),
        ("key;drop", False),
    ],
)
def test_pattern_check(pattern, ok):
    assert bool(PATTERN_RE.match(pattern)) is ok
