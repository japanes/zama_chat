"""Prompt versions on PostgreSQL: seeding the legacy prompts, tenant override, new
versions and rollback."""

import json

import pytest
from sqlalchemy import select

from app.db.models import Prompt
from app.errors import AppError
from app.prompts.seed import (
    LEGACY_DIR,
    REWRITE_KEY,
    SERVICE_SCRIPTS,
    build_rewrite,
    legacy_prompts,
    seed_prompts,
)
from app.prompts.store import activate_version, active_prompts, load_prompts, save_version
from tests.factories import make_tenant

pytestmark = pytest.mark.db


async def _versions(sm, key: str) -> list[tuple[int, bool]]:
    async with sm() as session:
        rows = (
            await session.execute(
                select(Prompt)
                .where(Prompt.key == key, Prompt.tenant_id.is_(None))
                .order_by(Prompt.version)
            )
        ).scalars()
        return [(row.version, row.is_active) for row in rows]


async def test_seed_is_idempotent(services):
    expected = set(legacy_prompts())
    async with services.sm() as session, session.begin():
        first = await seed_prompts(session)
    assert set(first.created) == expected
    assert first.unchanged == []

    async with services.sm() as session, session.begin():
        second = await seed_prompts(session)
    assert second.created == []
    assert set(second.unchanged) == expected
    assert await _versions(services.sm, "system_prompt") == [(1, True)]


async def test_seed_has_the_service_and_rewrite_prompts(services):
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    keys = [REWRITE_KEY, "system_prompt", "user_prompt", *SERVICE_SCRIPTS.values()]
    found = await load_prompts(services.sm, None, keys, required=keys)
    assert set(found) == set(keys)
    assert all(p.version == 1 and p.tenant_id is None for p in found.values())
    # the service prompt is the first step of the legacy script, not the whole file
    assert not found["service_greeting_only"].body.lstrip().startswith("{")


async def test_changed_file_adds_a_version(services, tmp_path):
    # a copy of the legacy dir with one prompt changed
    directory = tmp_path / "prompts"
    (directory / "scripts").mkdir(parents=True)
    for path in LEGACY_DIR.glob("*.txt"):
        (directory / path.name).write_text(path.read_text("utf-8"), "utf-8")
    for name in SERVICE_SCRIPTS:
        source = LEGACY_DIR / "scripts" / name
        (directory / "scripts" / name).write_text(source.read_text("utf-8"), "utf-8")

    async with services.sm() as session, session.begin():
        await seed_prompts(session, directory)
    (directory / "quality_manager.txt").write_text("changed", "utf-8")
    async with services.sm() as session, session.begin():
        result = await seed_prompts(session, directory)
    assert result.created == ["quality_manager"]
    assert await _versions(services.sm, "quality_manager") == [(1, False), (2, True)]


async def test_tenant_version_wins(services):
    bundle = await make_tenant(services.sm)
    other = await make_tenant(services.sm, slug="other")
    async with services.sm() as session, session.begin():
        await save_version(session, key="system_prompt", body="shared")
        await save_version(
            session, key="system_prompt", body="acme only", tenant_id=bundle.tenant_id
        )
    async with services.sm() as session:
        mine = await active_prompts(session, bundle.tenant_id, ["system_prompt"])
        theirs = await active_prompts(session, other.tenant_id, ["system_prompt"])
        shared = await active_prompts(session, None, ["system_prompt"])
    assert mine["system_prompt"].body == "acme only"
    assert mine["system_prompt"].tenant_id == bundle.tenant_id
    assert theirs["system_prompt"].body == "shared"
    assert shared["system_prompt"].body == "shared"


async def test_new_version_deactivates_the_old_one(services):
    async with services.sm() as session, session.begin():
        first = await save_version(session, key="k", body="v1", author="a@x")
        second = await save_version(session, key="k", body="v2", note="shorter")
    assert (first.version, second.version) == (1, 2)
    assert await _versions(services.sm, "k") == [(1, False), (2, True)]
    found = await load_prompts(services.sm, None, ["k"])
    assert found["k"].body == "v2"


async def test_inactive_draft_does_not_replace_the_active_one(services):
    async with services.sm() as session, session.begin():
        await save_version(session, key="k", body="v1")
        await save_version(session, key="k", body="draft", activate=False)
    assert await _versions(services.sm, "k") == [(1, True), (2, False)]


async def test_versions_are_counted_per_owner(services):
    bundle = await make_tenant(services.sm)
    async with services.sm() as session, session.begin():
        await save_version(session, key="k", body="shared 1")
        await save_version(session, key="k", body="shared 2")
        own = await save_version(session, key="k", body="own 1", tenant_id=bundle.tenant_id)
    assert own.version == 1
    # the tenant's version did not switch the shared one off
    assert await _versions(services.sm, "k") == [(1, False), (2, True)]


async def test_rollback(services):
    async with services.sm() as session, session.begin():
        await save_version(session, key="k", body="v1")
        await save_version(session, key="k", body="v2")
    async with services.sm() as session, session.begin():
        await activate_version(session, key="k", version=1)
    assert await _versions(services.sm, "k") == [(1, True), (2, False)]
    assert (await load_prompts(services.sm, None, ["k"]))["k"].body == "v1"


async def test_rollback_to_a_missing_version(services):
    async with services.sm() as session, session.begin():
        await save_version(session, key="k", body="v1")
    with pytest.raises(AppError) as err:
        async with services.sm() as session, session.begin():
            await activate_version(session, key="k", version=7)
    assert err.value.code == "prompt.version_not_found"
    assert err.value.params == {"key": "k", "version": 7}
    assert err.value.status == 404


async def test_missing_required_prompt(services):
    with pytest.raises(AppError) as err:
        await load_prompts(services.sm, None, ["rewrite"], required=["rewrite"])
    assert err.value.code == "prompt.not_found"
    assert err.value.params == {"key": "rewrite"}
    assert err.value.status == 500


async def test_missing_optional_prompt_is_just_absent(services):
    assert await load_prompts(services.sm, None, ["service_goodbye_only"]) == {}


def test_rewrite_prompt_shape():
    data = json.loads(build_rewrite(LEGACY_DIR))
    assert data["dialogue"] == []
    assert data["selected_line"] == ""
    assert data["answer_language"] == ""
    assert data["return"]["example"] == {"question": ["user message"], "language": "en"}
    assert set(data["rules"]) == {"common", "question", "language"}
    assert data["rules"]["question"]["answer_language"]
    assert data["rules"]["language"]
    # the role/type parts of the legacy context manager are left out
    assert not any("'type' and 'role'" in rule for rule in data["rules"]["common"])
