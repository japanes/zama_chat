"""Prompts and scenarios in the admin: versions, rollback, tenant overrides, checks."""

import pytest

from app.prompts.store import active_prompts
from tests.admin_helpers import admin_headers
from tests.factories import make_tenant
from tests.scenario_helpers import interview_data

pytestmark = pytest.mark.db

LANGS = {"languages": ["uk", "en"]}


@pytest.fixture
async def tenant(services):
    return await make_tenant(services.sm, settings=LANGS)


@pytest.fixture
async def root(services):
    return await admin_headers(services, "root@example.com", superadmin=True)


@pytest.fixture
async def adm(services, tenant):
    return await admin_headers(services, "adm@example.com", tenant_id=tenant.tenant_id)


async def _version(client, headers, key, body, tenant_id=None, activate=True):
    payload = {"body": body, "activate": activate}
    if tenant_id is not None:
        payload["tenant_id"] = str(tenant_id)
    return await client.post(f"/v1/admin/prompts/{key}/versions", json=payload, headers=headers)


# --- prompts ------------------------------------------------------------------------------


async def test_prompt_versions_and_rollback(client, services, root):
    assert (await _version(client, root, "system_prompt", "v1")).json()["prompt"]["version"] == 1
    assert (await _version(client, root, "system_prompt", "v2")).json()["prompt"]["version"] == 2

    async with services.sm() as session:
        assert (await active_prompts(session, None, ["system_prompt"]))[
            "system_prompt"
        ].body == "v2"

    resp = await client.post(
        "/v1/admin/prompts/system_prompt/activate", json={"version": 1}, headers=root
    )
    assert resp.status_code == 200
    async with services.sm() as session:
        assert (await active_prompts(session, None, ["system_prompt"]))[
            "system_prompt"
        ].body == "v1"

    versions = (await client.get("/v1/admin/prompts/system_prompt/versions", headers=root)).json()
    assert [(v["version"], v["is_active"]) for v in versions["items"]] == [(2, False), (1, True)]


async def test_draft_version_does_not_go_live(client, services, root):
    await _version(client, root, "system_prompt", "live")
    await _version(client, root, "system_prompt", "draft", activate=False)
    async with services.sm() as session:
        assert (await active_prompts(session, None, ["system_prompt"]))[
            "system_prompt"
        ].body == "live"


async def test_rollback_to_a_missing_version(client, services, root):
    await _version(client, root, "system_prompt", "v1")
    resp = await client.post(
        "/v1/admin/prompts/system_prompt/activate", json={"version": 9}, headers=root
    )
    assert resp.json()["error"]["code"] == "prompt.version_not_found"


async def test_tenant_override_and_reset(client, services, root, adm, tenant):
    tid = tenant.tenant_id
    await _version(client, root, "system_prompt", "shared")
    resp = await _version(client, adm, "system_prompt", "ours", tenant_id=tid)
    assert resp.status_code == 200

    items = (await client.get(f"/v1/admin/prompts?tenant_id={tid}", headers=adm)).json()["items"]
    item = next(i for i in items if i["key"] == "system_prompt")
    assert item["effective"] == "own"
    async with services.sm() as session:
        assert (await active_prompts(session, tid, ["system_prompt"]))[
            "system_prompt"
        ].body == "ours"

    resp = await client.delete(
        f"/v1/admin/prompts/system_prompt/override?tenant_id={tid}", headers=adm
    )
    assert resp.status_code == 200
    async with services.sm() as session:
        assert (await active_prompts(session, tid, ["system_prompt"]))[
            "system_prompt"
        ].body == "shared"


async def test_tenant_admin_cannot_change_the_shared_prompt(client, services, root, adm):
    await _version(client, root, "system_prompt", "shared")
    resp = await _version(client, adm, "system_prompt", "hacked")
    assert resp.status_code == 403
    # but may read the shared ones (to compare with the own override)
    assert (await client.get("/v1/admin/prompts", headers=adm)).status_code == 200


async def test_tenant_cannot_invent_a_prompt_key(client, services, adm, tenant):
    resp = await _version(client, adm, "my_new_prompt", "x", tenant_id=tenant.tenant_id)
    assert resp.json()["error"]["code"] == "prompt.unknown_key"


async def test_json_prompt_must_stay_json(client, services, root):
    resp = await _version(client, root, "context_manager_question", '{"model": "x",')
    assert resp.json()["error"]["code"] == "prompt.invalid_json"


async def test_bad_prompt_key(client, services, root):
    resp = await _version(client, root, "Bad-Key", "x")
    assert resp.json()["error"]["code"] == "prompt.key_invalid"


# --- scenarios ----------------------------------------------------------------------------


def _url(tenant, tail: str = "") -> str:
    return f"/v1/admin/tenants/{tenant.tenant_id}/scenarios{tail}"


async def test_save_makes_versions_only_on_change(client, services, adm, tenant):
    data = interview_data()
    first = await client.post(_url(tenant), json={"definition": data}, headers=adm)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    assert first.json()["scenario"]["version"] == 1

    same = await client.post(_url(tenant), json={"definition": data}, headers=adm)
    assert same.json()["created"] is False

    changed = interview_data(title={"uk": "Нове", "en": "New"})
    second = await client.post(_url(tenant), json={"definition": changed}, headers=adm)
    assert second.json()["scenario"]["version"] == 2

    detail = (await client.get(_url(tenant, "/interview"), headers=adm)).json()
    assert detail["current"]["version"] == 2
    assert [v["version"] for v in detail["versions"]] == [2, 1]


async def test_rollback_scenario_version(client, services, adm, tenant):
    await client.post(_url(tenant), json={"definition": interview_data()}, headers=adm)
    await client.post(
        _url(tenant),
        json={"definition": interview_data(title={"uk": "Нове", "en": "New"})},
        headers=adm,
    )
    resp = await client.post(_url(tenant, "/interview/activate"), json={"version": 1}, headers=adm)
    assert resp.status_code == 200
    detail = (await client.get(_url(tenant, "/interview"), headers=adm)).json()
    assert detail["current"]["version"] == 1


async def test_save_from_yaml(client, services, adm, tenant):
    text = """
key: hello
title: {uk: Привіт, en: Hello}
language: uk
start:
  questions:
    - key: greets
      ask: {uk: Клієнт вітається?, en: Does the client greet?}
fields:
  - key: name
    question: {uk: Як вас звати?, en: What is your name?}
"""
    resp = await client.post(_url(tenant), json={"yaml": text}, headers=adm)
    assert resp.status_code == 200, resp.text
    assert resp.json()["scenario"]["key"] == "hello"


async def test_broken_yaml_points_at_the_line(client, services, adm, tenant):
    resp = await client.post(_url(tenant), json={"yaml": "key: a\n  bad: [\n"}, headers=adm)
    error = resp.json()["error"]
    assert error["code"] == "scenario.yaml_invalid"
    assert error["params"]["line"] is not None


async def test_check_reports_the_error_without_saving(client, services, adm, tenant):
    data = interview_data()
    data["start"]["threshold"] = 99
    resp = await client.post(_url(tenant, "/check"), json={"definition": data}, headers=adm)
    out = resp.json()
    assert out["valid"] is False
    assert out["error"]["code"] == "scenario.threshold_too_high"
    listed = (await client.get(_url(tenant), headers=adm)).json()
    assert listed["items"] == []


async def test_missing_language_is_a_warning(client, services, adm, tenant):
    data = interview_data()
    data["fields"][1]["question"] = {"uk": "Що ви продаєте?"}  # no en
    resp = await client.post(_url(tenant, "/check"), json={"definition": data}, headers=adm)
    out = resp.json()
    assert out["valid"] is True
    assert any(w["code"] == "scenario.text_missing_language" for w in out["warnings"])


async def test_channel_scenarios_order(client, services, adm, tenant):
    for key in ("interview", "second"):
        await client.post(_url(tenant), json={"definition": interview_data(key=key)}, headers=adm)
    url = f"/v1/admin/tenants/{tenant.tenant_id}/channels/{tenant.channel_id}/scenarios"
    resp = await client.put(
        url,
        json={
            "items": [
                {"key": "second", "is_enabled": True},
                {"key": "interview", "is_enabled": False},
            ]
        },
        headers=adm,
    )
    assert resp.status_code == 200, resp.text
    items = (await client.get(url, headers=adm)).json()["items"]
    assert [(i["key"], i["is_enabled"]) for i in items] == [
        ("second", True),
        ("interview", False),
    ]

    unknown = await client.put(
        url, json={"items": [{"key": "nope", "is_enabled": True}]}, headers=adm
    )
    assert unknown.json()["error"]["code"] == "scenario.not_found"
    dup = await client.put(
        url,
        json={
            "items": [
                {"key": "second", "is_enabled": True},
                {"key": "second", "is_enabled": False},
            ]
        },
        headers=adm,
    )
    assert dup.json()["error"]["code"] == "channel.scenario_duplicate"


async def test_disable_scenario(client, services, adm, tenant):
    await client.post(_url(tenant), json={"definition": interview_data()}, headers=adm)
    assert (await client.post(_url(tenant, "/interview/disable"), headers=adm)).status_code == 200
    again = await client.post(_url(tenant, "/interview/disable"), headers=adm)
    assert again.json()["error"]["code"] == "scenario.not_active"
