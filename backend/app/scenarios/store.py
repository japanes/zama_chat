"""Scenario versions in the DB, the channel's scenario list and what a turn needs:
the channel's scenarios (active versions, in order) and the conversation's open runs."""

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import new_id
from app.db.models import OPEN_STATUSES, ChannelScenario, RunStatus, Scenario, ScenarioRun
from app.errors import AppError
from app.logging import get_logger
from app.scenarios.schema import FieldDef, Option, ScenarioDef, parse_definition

log = get_logger(__name__)


@dataclass(frozen=True)
class LoadedScenario:
    id: uuid.UUID
    key: str
    version: int
    definition: ScenarioDef
    position: int = 0


@dataclass
class OpenRun:
    id: uuid.UUID
    scenario: LoadedScenario  # the version the run was started with
    status: str
    fields: dict[str, Any]
    asking: str | None
    delivery_seq: int
    started_at: datetime
    last_activity_at: datetime
    # stage 6: confirmed_at, waiting_for, waiting_since, external, events_seen
    data: dict[str, Any] = field(default_factory=dict)
    # the run's own fields and options (effective_definition); `scenario.definition`
    # already has them
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def added(self) -> list[str]:
        return added_keys(self.extra)


@dataclass
class ScenarioView:
    """What the scenarios step of a turn starts from."""

    channel: list[LoadedScenario] = field(default_factory=list)  # enabled, in order
    runs: list[OpenRun] = field(default_factory=list)  # open (focus / paused / waiting)
    expired: list[OpenRun] = field(default_factory=list)  # resume window ran out

    @property
    def active(self) -> bool:
        return bool(self.channel or self.runs or self.expired)

    @property
    def focus(self) -> OpenRun | None:
        return next((r for r in self.runs if r.status == RunStatus.FOCUS), None)

    @property
    def only_scenario(self) -> bool:
        """An open run of a scenario with `only_scenario`: the turn is the scenarios' alone —
        no general replies, searches or starts by themselves."""
        return any(r.scenario.definition.only_scenario for r in self.runs)

    def run_for(self, key: str) -> OpenRun | None:
        return next((r for r in self.runs if r.scenario.key == key), None)


def expires_at(
    defn: ScenarioDef,
    last_activity_at: datetime,
    *,
    status: str | None = None,
    data: dict[str, Any] | None = None,
) -> datetime | None:
    """When an open run is abandoned: a waiting run — `waiting.hours` after it started
    waiting; others — `resume: within_hours` after the last activity. None = never."""
    if status == RunStatus.WAITING:
        if defn.waiting.hours is None:
            return None
        since = (data or {}).get("waiting_since")
        start = datetime.fromisoformat(since) if isinstance(since, str) else last_activity_at
        return start + timedelta(hours=defn.waiting.hours)
    resume = defn.resume
    if resume.mode != "within_hours" or resume.hours is None:
        return None
    return last_activity_at + timedelta(hours=resume.hours)


def is_expired(
    defn: ScenarioDef,
    last_activity_at: datetime,
    now: datetime,
    *,
    status: str | None = None,
    data: dict[str, Any] | None = None,
) -> bool:
    at = expires_at(defn, last_activity_at, status=status, data=data)
    return at is not None and now >= at


# extra["fields"]: field definitions + this key — when the caller added the field
ADDED_AT = "added_at"


def added_keys(extra: dict[str, Any] | None) -> list[str]:
    """Keys of the fields added while the run goes, in the order they came."""
    return [str(f["key"]) for f in (extra or {}).get("fields") or []]


def effective_definition(defn: ScenarioDef, extra: dict[str, Any] | None) -> ScenarioDef:
    """The run's fields: the scenario's, with the options the caller gave at the start for
    `options_from: caller`, then the fields added while the run goes (POST
    /v1/runs/{id}/fields). Without extra — the scenario as it is."""
    extra = extra or {}
    added = extra.get("fields") or []
    options = extra.get("options") or {}
    if not added and not options:
        return defn
    fields: list[FieldDef] = []
    for f in defn.fields:
        given = options.get(f.key)
        if f.options_from == "caller" and given:
            f = f.model_copy(update={"options": [Option.model_validate(o) for o in given]})
        fields.append(f)
    for raw in added:
        fields.append(FieldDef.model_validate({k: v for k, v in raw.items() if k != ADDED_AT}))
    return defn.model_copy(update={"fields": fields})


def run_scenario(scenario: LoadedScenario, extra: dict[str, Any] | None) -> LoadedScenario:
    """The scenario as this run sees it (effective_definition)."""
    if not extra:
        return scenario
    return replace(scenario, definition=effective_definition(scenario.definition, extra))


def _loaded(row: Scenario, position: int = 0) -> LoadedScenario | None:
    try:
        definition = parse_definition(row.definition)
    except AppError:
        # saved definitions are checked on save; a broken one must not break turns
        log.error("scenario.definition_broken", scenario_id=str(row.id), key=row.key)
        return None
    return LoadedScenario(row.id, row.key, row.version, definition, position)


async def load_view(
    sm: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    channel_id: uuid.UUID,
    conversation_id: uuid.UUID,
    now: datetime,
) -> ScenarioView:
    view = ScenarioView()
    async with sm() as session:
        rows = (
            await session.execute(
                select(Scenario, ChannelScenario.position)
                .join(
                    ChannelScenario,
                    (ChannelScenario.scenario_key == Scenario.key)
                    & (ChannelScenario.channel_id == channel_id),
                )
                .where(
                    Scenario.tenant_id == tenant_id,
                    Scenario.is_active,
                    ChannelScenario.is_enabled,
                )
                .order_by(ChannelScenario.position, Scenario.key)
            )
        ).all()
        for scenario, position in rows:
            if loaded := _loaded(scenario, position):
                view.channel.append(loaded)

        runs = (
            await session.execute(
                select(ScenarioRun, Scenario)
                .join(Scenario, Scenario.id == ScenarioRun.scenario_id)
                .where(
                    ScenarioRun.conversation_id == conversation_id,
                    ScenarioRun.status.in_(OPEN_STATUSES),
                )
                .order_by(ScenarioRun.started_at)
            )
        ).all()
    positions = {s.key: s.position for s in view.channel}
    for run, scenario in runs:
        loaded = _loaded(scenario, positions.get(scenario.key, 0))
        if loaded is None:
            continue
        try:
            loaded = run_scenario(loaded, run.extra)
        except ValueError:
            # checked when added; a broken one must not break turns
            log.error("scenario.run_extra_broken", run_id=str(run.id))
            continue
        item = OpenRun(
            id=run.id,
            scenario=loaded,
            status=run.status,
            fields=dict(run.fields or {}),
            asking=run.asking,
            delivery_seq=run.delivery_seq,
            started_at=run.started_at,
            last_activity_at=run.last_activity_at,
            data=dict(run.data or {}),
            extra=dict(run.extra or {}),
        )
        if is_expired(
            loaded.definition, run.last_activity_at, now, status=run.status, data=run.data
        ):
            view.expired.append(item)
        else:
            view.runs.append(item)
    return view


async def load_definition(session: AsyncSession, scenario_id: uuid.UUID) -> ScenarioDef:
    row = await session.get(Scenario, scenario_id)
    if row is None:
        raise AppError("scenario.not_found", {"id": str(scenario_id)}, status=404)
    return parse_definition(row.definition)


async def load_run_definition(session: AsyncSession, run: ScenarioRun) -> ScenarioDef:
    """The fields of a run as it goes: its scenario version + its own (extra)."""
    return effective_definition(await load_definition(session, run.scenario_id), run.extra)


def definition_json(defn: ScenarioDef) -> dict[str, Any]:
    return defn.model_dump(mode="json", exclude_none=True)


def scenario_title(defn: ScenarioDef) -> str:
    return defn.text(defn.title, defn.language).text


async def is_working_version(
    session: AsyncSession, tenant_id: uuid.UUID, defn: ScenarioDef
) -> bool:
    """The definition is the working version as it is: saving it would change nothing."""
    active = (
        await session.execute(
            select(Scenario.definition).where(
                Scenario.tenant_id == tenant_id, Scenario.key == defn.key, Scenario.is_active
            )
        )
    ).scalar_one_or_none()
    return active is not None and active == definition_json(defn)


async def save_scenario(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    defn: ScenarioDef,
    *,
    author: str | None = None,
    note: str | None = None,
    activate: bool = True,
) -> tuple[Scenario, bool]:
    """A new version when the definition differs from the active one.
    Returns (the active / new row, created)."""
    data = definition_json(defn)
    # one writer per (tenant, key): versions are counted under a lock
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"scenario:{tenant_id}:{defn.key}")))
    )
    active = (
        await session.execute(
            select(Scenario).where(
                Scenario.tenant_id == tenant_id, Scenario.key == defn.key, Scenario.is_active
            )
        )
    ).scalar_one_or_none()
    if active is not None and active.definition == data:
        return active, False
    last = (
        await session.execute(
            select(func.max(Scenario.version)).where(
                Scenario.tenant_id == tenant_id, Scenario.key == defn.key
            )
        )
    ).scalar_one()
    if activate and active is not None:
        active.is_active = False
        await session.flush()
    row = Scenario(
        tenant_id=tenant_id,
        key=defn.key,
        version=(last or 0) + 1,
        title=scenario_title(defn)[:300],
        definition=data,
        is_active=activate,
        author=author,
        note=note,
    )
    session.add(row)
    await session.flush()
    return row, True


async def enable_in_channel(
    session: AsyncSession, *, tenant_id: uuid.UUID, channel_id: uuid.UUID, keys: list[str]
) -> list[str]:
    """Add scenarios to the end of the channel's list (already listed ones stay where
    they are, but are switched on). Returns the keys added."""
    position = (
        await session.execute(
            select(func.coalesce(func.max(ChannelScenario.position), -1)).where(
                ChannelScenario.channel_id == channel_id
            )
        )
    ).scalar_one()
    added = []
    for key in keys:
        position += 1
        inserted = (
            await session.execute(
                insert(ChannelScenario)
                .values(
                    id=new_id(),
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    scenario_key=key,
                    position=position,
                    is_enabled=True,
                )
                .on_conflict_do_nothing(index_elements=["channel_id", "scenario_key"])
                .returning(ChannelScenario.id)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            added.append(key)
        else:
            await session.execute(
                update(ChannelScenario)
                .where(
                    ChannelScenario.channel_id == channel_id, ChannelScenario.scenario_key == key
                )
                .values(is_enabled=True)
            )
    return added
