import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace

from app.db.models import RunStatus, ScenarioRun
from app.results.delivery import (
    MAX_ATTEMPTS,
    RETRY_DELAYS_S,
    encode_body,
    is_permanent,
    retry_delay,
    sign,
)
from app.results.service import build_payload, collected, result_status
from app.scenarios.fields import FILLED, FieldState
from app.scenarios.schema import ActionEvent
from tests.scenario_helpers import make_def, states
from tests.turn_ctx import T0

DEFN = make_def()


def test_status_by_event_then_by_run_status_and_explicit_wins():
    assert result_status(ActionEvent.COMPLETED, RunStatus.COMPLETED) == "completed"
    assert result_status(ActionEvent.SILENCE, RunStatus.FOCUS) == "incomplete"
    assert result_status(ActionEvent.ABANDONED, RunStatus.ABANDONED) == "abandoned"
    assert result_status(ActionEvent.FIELD_FILLED, RunStatus.PAUSED) == "in_progress"
    assert result_status(ActionEvent.ALL_FIELDS, RunStatus.WAITING) == "waiting"
    assert result_status(ActionEvent.SILENCE, RunStatus.FOCUS, "draft") == "draft"


def test_collected_splits_fields_partial_and_missing():
    raw = states(
        DEFN,
        niche="торти",
        offer=("partial", "щось", ["x"]),
        positioning=FieldState(state=FILLED, value="дешевше", needs_recheck=True),
    )
    assert collected(DEFN, raw) == {
        "fields": {"niche": "торти"},
        "partial": {"offer": "щось", "positioning": "дешевше"},
        "missing": ["offer", "positioning"],
        "refs": {},
        "normalized": {},
        "hidden": [],
        "closed": [],
    }


def test_collected_of_nothing():
    assert collected(DEFN, None) == {
        "fields": {},
        "partial": {},
        "missing": ["niche", "offer", "positioning"],
        "refs": {},
        "normalized": {},
        "hidden": [],
        "closed": [],
    }


def test_payload_has_the_result_id_seq_and_languages():
    run = ScenarioRun(
        id=uuid.uuid4(),
        channel_id=uuid.uuid4(),
        scenario_key="interview",
        scenario_version=3,
        status=RunStatus.COMPLETED,
        fields=states(DEFN, niche="торти", offer="торти", positioning="дешевше"),
        client_language="ru",
        reply_language="uk",
        started_at=T0,
        finished_at=T0,
    )
    contact = SimpleNamespace(external_id="c1", username="olha", name="Olha")
    channel = SimpleNamespace(kind="http")
    payload = build_payload(
        run,
        DEFN,
        contact=contact,
        channel=channel,
        event=ActionEvent.COMPLETED,
        status="completed",
        delivery_id="d1",
        seq=2,
    )
    assert payload["event"] == "scenario.completed"
    assert payload["result_id"] == str(run.id)
    assert (payload["delivery_id"], payload["seq"]) == ("d1", 2)
    assert (payload["scenario"], payload["scenario_version"]) == ("interview", 3)
    assert payload["client"] == {
        "channel": "http",
        "channel_id": str(run.channel_id),
        "id": "c1",
        "username": "olha",
        "name": "Olha",
    }
    assert payload["fields"] == {"niche": "торти", "offer": "торти", "positioning": "дешевше"}
    assert payload["missing"] == []
    assert (payload["client_language"], payload["reply_language"]) == ("ru", "uk")
    assert payload["started_at"] == T0.isoformat()


def test_signature_is_hmac_sha256_of_the_exact_body():
    body = encode_body({"b": "Привіт", "a": 1})
    assert body == '{"b":"Привіт","a":1}'.encode()
    expected = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert sign("s3cret", body) == f"sha256={expected}"
    assert json.loads(body) == {"b": "Привіт", "a": 1}


def test_retry_schedule():
    assert [retry_delay(i) for i in range(1, 7)] == [60, 300, 1800, 7200, 21600, 21600]
    assert len(RETRY_DELAYS_S) + 1 == MAX_ATTEMPTS


def test_which_codes_are_not_retried():
    assert is_permanent(400)
    assert is_permanent(404)
    assert not is_permanent(408)
    assert not is_permanent(429)
    assert not is_permanent(500)
    assert not is_permanent(503)
