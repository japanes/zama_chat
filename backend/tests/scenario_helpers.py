"""Scenario definitions, views and decision sets for scenario tests."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.models import (
    DecisionProvider,
    DecisionType,
    Delivery,
    Endpoint,
    Job,
    RunStatus,
    ScenarioRun,
)
from app.decisions.engine import Answer, DecisionSet
from app.scenarios.fields import FILLED, PARTIAL, FieldState, dump_states
from app.scenarios.schema import ScenarioDef, check_definition, parse_definition
from app.scenarios.store import (
    LoadedScenario,
    OpenRun,
    ScenarioView,
    enable_in_channel,
    save_scenario,
)
from tests.turn_ctx import T0

# FakeDecider answers for a turn with scenarios: nothing fires, every filter passes.
# A test overrides single keys (exact keys win over the patterns).
ROUTE_DEFAULTS: dict[str, Any] = {
    "service_type": "none",
    "wants_human": False,
    "start:*": False,
    "field:*": False,
    "stop": "none",
    "change_field": "none",
    "client_question": False,
    "filter:*": True,
}


def interview_data(**over: Any) -> dict[str, Any]:
    """A small interview: niche → offer → positioning (after niche + offer)."""
    data: dict[str, Any] = {
        "key": "interview",
        "title": {"uk": "Інтерв'ю", "en": "Interview"},
        "language": "uk",
        "start": {
            "questions": [
                {
                    "key": "wants_help",
                    "ask": {"uk": "Клієнт хоче допомоги?", "en": "Does the client want help?"},
                    "weight": 2,
                },
                {"field": "niche"},
                {"field": "offer"},
            ],
            "threshold": 2,
        },
        "fields": [
            {
                "key": "niche",
                "question": {"uk": "Яка у вас ніша?", "en": "What is your niche?"},
                "filters": [
                    {
                        "key": "concrete",
                        "ask": {"uk": "Ніша конкретна?", "en": "Is the niche specific?"},
                    }
                ],
            },
            {"key": "offer", "question": {"uk": "Що ви продаєте?", "en": "What do you sell?"}},
            {
                "key": "positioning",
                "question": {"uk": "Чим ви відрізняєтесь?", "en": "How are you different?"},
                "ask_after": ["niche", "offer"],
                "filters": [
                    {
                        "key": "fits",
                        "ask": {"uk": "Відмінність пасує?", "en": "Does it fit?"},
                    }
                ],
            },
        ],
        "actions": [
            {"on": "completed", "do": "http_post", "endpoint": "results_api"},
            {
                "on": "silence",
                "after_minutes": 15,
                "do": "http_post",
                "endpoint": "results_api",
                "status": "incomplete",
            },
        ],
    }
    data.update(over)
    return data


def booking_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "key": "booking",
        "title": {"uk": "Запис", "en": "Booking"},
        "language": "uk",
        "start": {
            "questions": [
                {"key": "wants_booking", "ask": {"uk": "Хоче записатися?", "en": "Book?"}}
            ],
        },
        "fields": [
            {"key": "service", "question": {"uk": "Яка послуга?", "en": "Which service?"}},
            {"key": "day", "question": {"uk": "Який день?", "en": "Which day?"}},
        ],
    }
    data.update(over)
    return data


def make_def(data: dict[str, Any] | None = None) -> ScenarioDef:
    return parse_definition(data or interview_data())


def loaded(defn: ScenarioDef, position: int = 0, version: int = 1) -> LoadedScenario:
    return LoadedScenario(uuid.uuid4(), defn.key, version, defn, position)


def states(defn: ScenarioDef, **values: Any) -> dict[str, Any]:
    """Raw `fields` JSON: key=value → filled; key=("partial", value, [failed]) → partial."""
    out: dict[str, FieldState] = {f.key: FieldState() for f in defn.fields}
    for key, value in values.items():
        if isinstance(value, tuple):
            _, text, failed = value
            out[key] = FieldState(state=PARTIAL, value=text, failed_filters=list(failed))
        elif isinstance(value, FieldState):
            out[key] = value
        else:
            out[key] = FieldState(state=FILLED, value=value)
    return dump_states(out)


def open_run(
    scenario: LoadedScenario,
    status: str = RunStatus.FOCUS,
    *,
    fields: dict[str, Any] | None = None,
    asking: str | None = None,
    started_at: datetime = T0,
    last_activity_at: datetime | None = None,
) -> OpenRun:
    return OpenRun(
        id=uuid.uuid4(),
        scenario=scenario,
        status=status,
        fields=fields or {},
        asking=asking,
        delivery_seq=0,
        started_at=started_at,
        last_activity_at=last_activity_at or started_at,
    )


def view(
    channel: list[LoadedScenario], runs: list[OpenRun] = (), expired: list[OpenRun] = ()
) -> ScenarioView:
    return ScenarioView(channel=list(channel), runs=list(runs), expired=list(expired))


def decisions(values: dict[str, Any]) -> DecisionSet:
    out = DecisionSet()
    for key, value in values.items():
        kind = DecisionType.BOOLEAN if isinstance(value, bool) else DecisionType.CHOICE
        out.answers[key] = Answer(
            key=key,
            type=kind,
            value=value,
            confidence=0.99,
            provider=DecisionProvider.FAKE,
            model="fake",
        )
    return out


# --- DB helpers ---------------------------------------------------------------------------


async def install_scenario(sm, tenant_id, channel_id, data: dict[str, Any]) -> uuid.UUID:
    """Save the definition (checked like the loader does) and switch it on in the channel."""
    defn = parse_definition(data)
    check_definition(defn, [])
    async with sm() as session, session.begin():
        row, _ = await save_scenario(session, tenant_id, defn, author="test")
        await enable_in_channel(
            session, tenant_id=tenant_id, channel_id=channel_id, keys=[defn.key]
        )
        return row.id


async def make_endpoint(
    services,
    tenant_id: uuid.UUID,
    *,
    url: str,
    name: str = "results_api",
    secret: str | None = None,
    headers: dict[str, str] | None = None,
) -> uuid.UUID:
    async with services.sm() as session, session.begin():
        endpoint = Endpoint(
            tenant_id=tenant_id,
            name=name,
            url=url,
            secret_enc=services.crypto.encrypt(secret) if secret else None,
            headers=headers or {},
        )
        session.add(endpoint)
        await session.flush()
        return endpoint.id


async def runs_of(sm, conversation_id: uuid.UUID | str) -> list[ScenarioRun]:
    async with sm() as session:
        query = (
            select(ScenarioRun)
            .where(ScenarioRun.conversation_id == uuid.UUID(str(conversation_id)))
            .order_by(ScenarioRun.started_at, ScenarioRun.id)
        )
        return list((await session.execute(query)).scalars())


async def deliveries_of(sm, run_id: uuid.UUID) -> list[Delivery]:
    async with sm() as session:
        query = select(Delivery).where(Delivery.run_id == run_id).order_by(Delivery.seq)
        return list((await session.execute(query)).scalars())


async def jobs_like(sm, prefix: str) -> list[Job]:
    async with sm() as session:
        query = (
            select(Job).where(Job.dedupe_key.startswith(prefix)).order_by(Job.created_at, Job.id)
        )
        return list((await session.execute(query)).scalars())
