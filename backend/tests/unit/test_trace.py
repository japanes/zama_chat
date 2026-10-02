"""Turn trace: steps with timing, errors, notes."""

import pytest

from app.core.trace import Trace


def test_steps_and_notes():
    trace = Trace()
    with trace.step("rewrite", model="m") as step:
        step["llm_call_id"] = "c1"
    trace.note("route_summary", {"wants_human": False})
    data = trace.to_dict()
    (entry,) = data["steps"]
    assert entry["step"] == "rewrite"
    assert entry["model"] == "m"
    assert entry["llm_call_id"] == "c1"
    assert entry["ms"] >= 0
    assert data["route_summary"] == {"wants_human": False}


def test_failed_step_is_kept_with_error():
    trace = Trace()
    with pytest.raises(ValueError), trace.step("route"):
        raise ValueError("bad answer")
    (entry,) = trace.to_dict()["steps"]
    assert entry["error"] == "ValueError: bad answer"
    assert "ms" in entry
