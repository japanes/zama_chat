"""Merge pause: when the turn of an asynchronous channel runs."""

from datetime import UTC, datetime, timedelta

from app.core.merge import DEFAULT_MAX_WAIT_S, DEFAULT_PAUSE_S, merge_times, turn_run_at

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


def test_first_message_waits_the_pause():
    assert turn_run_at(NOW, None, 5, 20) == NOW + s(5)
    assert turn_run_at(NOW, NOW, 5, 20) == NOW + s(5)


def test_each_new_message_moves_the_turn():
    first = NOW - s(6)  # two messages 3 s apart before this one
    assert turn_run_at(NOW, first, 5, 20) == NOW + s(5)


def test_never_later_than_max_wait_after_the_first_message():
    first = NOW - s(18)
    assert turn_run_at(NOW, first, 5, 20) == first + s(20)  # = NOW + 2 s


def test_max_wait_already_passed_runs_now():
    assert turn_run_at(NOW, NOW - s(30), 5, 20) == NOW


def test_merge_times_from_channel_settings():
    assert merge_times({}) == (DEFAULT_PAUSE_S, DEFAULT_MAX_WAIT_S)
    assert merge_times({"merge_pause_s": 3, "merge_max_wait_s": 12}) == (3.0, 12.0)
    # max wait is never shorter than the pause
    assert merge_times({"merge_pause_s": 8, "merge_max_wait_s": 2}) == (8.0, 8.0)
    # a negative pause is no pause
    assert merge_times({"merge_pause_s": -1}) == (0.0, DEFAULT_MAX_WAIT_S)
