"""Confidence threshold by question key."""

from app.decisions.policy import DEFAULT_THRESHOLD, DecisionPolicy


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
