"""Scenario files → versions in the DB → the channel's list → what a turn loads."""

import uuid

import pytest
import yaml
from sqlalchemy import select

from app.db.models import (
    ChannelKind,
    ChannelScenario,
    Contact,
    Conversation,
    RunStatus,
    Scenario,
    ScenarioRun,
    Tenant,
)
from app.errors import AppError
from app.scenarios.loader import active_scenarios, enable_loaded, load_path
from app.scenarios.store import load_view
from app.settings import PROJECT_ROOT
from tests.factories import make_channel, make_tenant
from tests.scenario_helpers import booking_data, install_scenario, interview_data

pytestmark = pytest.mark.db

LANGUAGES = {"languages": ["uk", "en"]}


def _write(path, data) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


async def _tenant(sm, bundle) -> Tenant:
    async with sm() as session:
        return await session.get(Tenant, bundle.tenant_id)


async def _load(sm, tenant, path):
    async with sm() as session, session.begin():
        return await load_path(session, tenant, path, author="test")


async def test_the_project_scenarios_load_and_switch_on(services):
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    tenant = await _tenant(services.sm, bundle)
    results = await _load(services.sm, tenant, PROJECT_ROOT / "scenarios")
    assert results and all(r.error is None for r in results)
    assert {r.key for r in results} >= {"offer_interview"}
    assert all(r.version == 1 and r.created for r in results)

    async with services.sm() as session, session.begin():
        added = await enable_loaded(session, tenant, results)
    assert set(added[str(bundle.channel_id)]) == {r.key for r in results}

    view = await load_view(
        services.sm,
        tenant_id=bundle.tenant_id,
        channel_id=bundle.channel_id,
        conversation_id=uuid.uuid4(),
        now=services.clock.now(),
    )
    assert [s.key for s in view.channel] == [r.key for r in results]


async def test_same_file_twice_keeps_the_version_a_change_makes_a_new_one(services, tmp_path):
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    tenant = await _tenant(services.sm, bundle)
    path = tmp_path / "interview.yaml"
    _write(path, interview_data())

    [first] = await _load(services.sm, tenant, path)
    [again] = await _load(services.sm, tenant, path)
    assert (first.version, first.created) == (1, True)
    assert (again.version, again.created) == (1, False)

    data = interview_data()
    data["fields"][1]["question"] = {"uk": "Що саме ви продаєте?", "en": "What exactly?"}
    _write(path, data)
    [changed] = await _load(services.sm, tenant, path)
    assert (changed.version, changed.created) == (2, True)

    async with services.sm() as session:
        rows = list(
            (
                await session.execute(
                    select(Scenario).where(Scenario.key == "interview").order_by(Scenario.version)
                )
            ).scalars()
        )
        active = await active_scenarios(session, bundle.tenant_id)
    assert [(r.version, r.is_active) for r in rows] == [(1, False), (2, True)]
    assert [s.version for s in active] == [2]


async def test_a_broken_file_does_not_stop_the_others(services, tmp_path):
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    tenant = await _tenant(services.sm, bundle)
    _write(tmp_path / "a_good.yaml", booking_data())
    bad = interview_data()
    bad["start"]["threshold"] = 99
    _write(tmp_path / "b_bad.yaml", bad)
    (tmp_path / "c_broken.yaml").write_text("key: [unclosed", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a scenario", encoding="utf-8")

    good, bad_result, broken = await _load(services.sm, tenant, tmp_path)
    assert (good.key, good.error) == ("booking", None)
    assert bad_result.error["code"] == "scenario.threshold_too_high"
    assert broken.error["code"] == "scenario.file_invalid"


async def test_warnings_for_missing_languages(services, tmp_path):
    bundle = await make_tenant(services.sm, settings={"languages": ["uk", "en", "ru"]})
    tenant = await _tenant(services.sm, bundle)
    path = tmp_path / "booking.yaml"
    _write(path, booking_data())
    [result] = await _load(services.sm, tenant, path)
    assert result.error is None
    assert {w["params"]["language"] for w in result.warnings} == {"ru"}


async def test_enable_in_one_channel_only(services):
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    other = await make_channel(services.sm, bundle.tenant_id, kind=ChannelKind.HTTP)
    tenant = await _tenant(services.sm, bundle)
    results = await _load(services.sm, tenant, PROJECT_ROOT / "scenarios")
    async with services.sm() as session, session.begin():
        added = await enable_loaded(session, tenant, results, channel_id=other)
    assert list(added) == [str(other)]
    async with services.sm() as session:
        rows = list((await session.execute(select(ChannelScenario))).scalars())
    assert {r.channel_id for r in rows} == {other}


async def test_enable_in_a_channel_of_another_tenant_is_not_found(services):
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    stranger = await make_tenant(services.sm, slug="other", settings=LANGUAGES)
    tenant = await _tenant(services.sm, bundle)
    with pytest.raises(AppError) as exc:
        async with services.sm() as session, session.begin():
            await enable_loaded(session, tenant, [], channel_id=stranger.channel_id)
    assert exc.value.code == "channel.not_found"


async def test_view_keeps_the_channel_order_and_finds_open_and_expired_runs(services):
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    booking = booking_data(resume={"mode": "within_hours", "hours": 1})
    interview_id = await install_scenario(
        services.sm, bundle.tenant_id, bundle.channel_id, interview_data()
    )
    booking_id = await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, booking)

    now = services.clock.now()
    async with services.sm() as session, session.begin():
        contact = Contact(tenant_id=bundle.tenant_id, channel_id=bundle.channel_id, external_id="c")
        session.add(contact)
        await session.flush()
        conv = Conversation(
            tenant_id=bundle.tenant_id, channel_id=bundle.channel_id, contact_id=contact.id
        )
        session.add(conv)
        await session.flush()
        for scenario_id, key, status in (
            (interview_id, "interview", RunStatus.FOCUS),
            (booking_id, "booking", RunStatus.PAUSED),
        ):
            session.add(
                ScenarioRun(
                    tenant_id=bundle.tenant_id,
                    channel_id=bundle.channel_id,
                    conversation_id=conv.id,
                    scenario_id=scenario_id,
                    scenario_key=key,
                    scenario_version=1,
                    status=status,
                    fields={},
                    events=[],
                    delivery_seq=0,
                    started_at=now,
                    last_activity_at=now,
                )
            )
        conversation_id = conv.id

    view = await load_view(
        services.sm,
        tenant_id=bundle.tenant_id,
        channel_id=bundle.channel_id,
        conversation_id=conversation_id,
        now=now,
    )
    assert [(s.key, s.position) for s in view.channel] == [("interview", 0), ("booking", 1)]
    assert {r.scenario.key for r in view.runs} == {"interview", "booking"}
    assert view.focus.scenario.key == "interview"
    assert view.expired == []

    later = await load_view(
        services.sm,
        tenant_id=bundle.tenant_id,
        channel_id=bundle.channel_id,
        conversation_id=conversation_id,
        now=services.clock.advance(hours=2),
    )
    assert [r.scenario.key for r in later.runs] == ["interview"]
    assert [r.scenario.key for r in later.expired] == ["booking"]


async def test_a_disabled_scenario_is_not_in_the_view(services):
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, interview_data())
    async with services.sm() as session, session.begin():
        row = (await session.execute(select(ChannelScenario))).scalar_one()
        row.is_enabled = False
    view = await load_view(
        services.sm,
        tenant_id=bundle.tenant_id,
        channel_id=bundle.channel_id,
        conversation_id=uuid.uuid4(),
        now=services.clock.now(),
    )
    assert view.channel == []
    assert not view.active
