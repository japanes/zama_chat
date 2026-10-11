"""Prompts and scenarios in the admin: versions, rollback, tenant overrides, checks."""

import json

import pytest
from sqlalchemy import select

from app.db.models import LlmCall, LlmRole, UserRole
from app.prompts.store import active_prompts
from tests.admin_helpers import admin_headers
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import interview_data, scenario_doc_example

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
      ask: {uk: "Клієнт вітається?", en: "Does the client greet?"}
fields:
  - key: name
    question: {uk: "Як вас звати?", en: "What is your name?"}
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


async def test_a_template_becomes_the_tenants_own_scenario(client, services, adm, tenant):
    url = f"/v1/admin/tenants/{tenant.tenant_id}/scenario-templates"
    templates = (await client.get(url, headers=adm)).json()["items"]
    by_key = {t["key"]: t for t in templates}
    assert "beauty_booking" in by_key
    assert [f["key"] for f in by_key["cancel_booking"]["fields"]] == ["booking", "reason"]

    # nothing is saved by looking at the templates
    assert (await client.get(_url(tenant), headers=adm)).json()["items"] == []

    # the editor takes the definition as a draft: it passes the check and is saved
    # under the tenant's own key
    draft = {**by_key["cancel_booking"]["definition"], "key": "my_cancel"}
    checked = (
        await client.post(_url(tenant, "/check"), json={"definition": draft}, headers=adm)
    ).json()
    assert checked["valid"] is True, checked
    saved = await client.post(_url(tenant), json={"definition": draft}, headers=adm)
    assert saved.status_code == 200, saved.text
    assert saved.json()["scenario"]["key"] == "my_cancel"


async def test_templates_are_for_those_who_edit_scenarios(client, services, tenant):
    operator = await admin_headers(
        services, "op@example.com", tenant_id=tenant.tenant_id, role=UserRole.OPERATOR
    )
    url = f"/v1/admin/tenants/{tenant.tenant_id}/scenario-templates"
    assert (await client.get(url, headers=operator)).status_code == 403
    assert (await client.get(url)).status_code == 401


# --- a scenario file: its description, and uploading one ----------------------------------


async def test_the_example_of_the_format_description_is_uploaded_and_works_at_once(
    client, services, adm, tenant
):
    # the file the documentation gives out («download an example»)
    example = scenario_doc_example("uk")
    assert "key: consultation_request" in example

    # what «upload a file» of the admin sends: the text of the file and where to switch on
    body = {"yaml": example, "enable_in_channels": [str(tenant.channel_id)]}
    source = {"yaml": example}
    checked = (await client.post(_url(tenant, "/check"), json=source, headers=adm)).json()
    assert checked["valid"] is True and checked["unchanged"] is False
    saved = (await client.post(_url(tenant), json=body, headers=adm)).json()
    assert saved["created"] is True
    assert saved["scenario"]["version"] == 1 and saved["scenario"]["is_active"] is True

    item = (await client.get(_url(tenant), headers=adm)).json()["items"][0]
    assert item["key"] == "consultation_request" and item["active_version"] == 1
    assert [(c["channel_id"], c["is_enabled"]) for c in item["channels"]] == [
        (str(tenant.channel_id), True)
    ]

    # the same file again changes nothing — the check says so before the upload;
    # a changed one is the next version, working at once
    checked = (await client.post(_url(tenant, "/check"), json=source, headers=adm)).json()
    assert checked["unchanged"] is True
    again = (await client.post(_url(tenant), json=body, headers=adm)).json()
    assert again["created"] is False and again["scenario"]["version"] == 1
    changed = {"yaml": example.replace("hours: 48", "hours: 24")}
    assert changed["yaml"] != example
    checked = (await client.post(_url(tenant, "/check"), json=changed, headers=adm)).json()
    assert checked["unchanged"] is False
    newer = (await client.post(_url(tenant), json=changed, headers=adm)).json()
    assert newer["created"] is True
    assert newer["scenario"]["version"] == 2 and newer["scenario"]["is_active"] is True


async def test_a_file_in_json_and_a_file_with_a_mark_of_its_encoding_are_taken(
    client, services, adm, tenant
):
    # an editor on Windows starts a file with an invisible mark of its encoding
    with_mark = "\ufeff" + json.dumps(interview_data())
    resp = await client.post(_url(tenant, "/check"), json={"yaml": with_mark}, headers=adm)
    assert resp.json()["valid"] is True, resp.text
    assert resp.json()["key"] == "interview"


# --- a draft from a description in words ---------------------------------------------------

DESCRIPTION = "Клієнт записується на стрижку. Треба дізнатися день і телефон."


def _sketch() -> dict:
    def text(uk: str, en: str) -> dict:
        return {"uk": uk, "en": en}

    return {
        "key": "haircut_booking",
        "title": text("Запис на стрижку", "Haircut booking"),
        "description": text("Бот збирає заявку на стрижку.", "The bot takes a haircut request."),
        "start": [text("Клієнт хоче записатися на стрижку?", "Does the client want a haircut?")],
        "questions": [
            {
                "key": "day",
                "name": text("День", "Day"),
                "type": "date",
                "question": text("На який день?", "Which day?"),
                "options": [],
            },
            {
                "key": "phone",
                "name": text("Телефон", "Phone"),
                "type": "phone",
                "question": text("Ваш номер телефону?", "Your phone number?"),
                "options": [],
            },
        ],
        "confirm": True,
    }


async def test_a_description_becomes_a_draft_the_editor_can_save(
    client, services, fake_llm, adm, tenant
):
    await make_model(services.sm, "openai/writer", roles=(LlmRole.REPLY,))
    fake_llm.push(chat_reply(json.dumps(_sketch(), ensure_ascii=False)))

    resp = await client.post(
        _url(tenant, "/draft"), json={"description": f"  {DESCRIPTION}  "}, headers=adm
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["model"] == "openai/writer" and out["warnings"] == []
    draft = out["definition"]
    assert (draft["key"], draft["language"]) == ("haircut_booking", "uk")
    assert [(f["key"], f["type"]) for f in draft["fields"]] == [("day", "date"), ("phone", "phone")]
    assert draft["fields"][0]["title"] == {"uk": "День", "en": "Day"}

    # the model got the person's words and a strict form to fill, in the tenant's languages
    [request] = fake_llm.requests
    assert DESCRIPTION in request["messages"][0]["content"]
    form = request["response_format"]["json_schema"]
    assert form["strict"] is True
    assert form["schema"]["properties"]["title"]["required"] == ["uk", "en"]

    # a draft is not saved; the editor saves it as an ordinary scenario
    assert (await client.get(_url(tenant), headers=adm)).json()["items"] == []
    saved = await client.post(_url(tenant), json={"definition": draft}, headers=adm)
    assert saved.status_code == 200, saved.text
    # the call is counted in the tenant's spending
    async with services.sm() as session:
        [call] = (await session.execute(select(LlmCall))).scalars().all()
    assert (call.tenant_id, call.role, call.prompt_key) == (
        tenant.tenant_id,
        "reply",
        "scenario_draft",
    )


async def test_a_draft_from_a_model_without_structured_output(
    client, services, fake_llm, adm, tenant
):
    await make_model(services.sm, "openai/plain", supports_json_schema=False)
    answer = "Ось чернетка:\n```json\n" + json.dumps(_sketch(), ensure_ascii=False) + "\n```"
    fake_llm.push(chat_reply(answer))

    resp = await client.post(_url(tenant, "/draft"), json={"description": DESCRIPTION}, headers=adm)
    assert resp.status_code == 200, resp.text
    assert resp.json()["definition"]["key"] == "haircut_booking"
    assert "response_format" not in fake_llm.requests[0]


async def test_a_draft_that_did_not_work_out_says_so(client, services, fake_llm, adm, tenant):
    await make_model(services.sm, "openai/plain", supports_json_schema=False)
    url = _url(tenant, "/draft")

    fake_llm.push(chat_reply("Вибачте, я не можу з цим допомогти."))
    resp = await client.post(url, json={"description": DESCRIPTION}, headers=adm)
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "scenario.draft_failed")

    fake_llm.push(chat_reply(json.dumps({**_sketch(), "questions": []})))
    resp = await client.post(url, json={"description": DESCRIPTION}, headers=adm)
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "scenario.draft_empty")

    assert (await client.post(url, json={"description": ""}, headers=adm)).status_code == 422
    assert fake_llm.calls == 2


async def test_a_draft_needs_a_model_and_the_right_to_edit(client, services, fake_llm, adm, tenant):
    url = _url(tenant, "/draft")
    resp = await client.post(url, json={"description": DESCRIPTION}, headers=adm)
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "llm.role_not_assigned")

    operator = await admin_headers(
        services, "op2@example.com", tenant_id=tenant.tenant_id, role=UserRole.OPERATOR
    )
    resp = await client.post(url, json={"description": DESCRIPTION}, headers=operator)
    assert resp.status_code == 403
    assert fake_llm.calls == 0


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
