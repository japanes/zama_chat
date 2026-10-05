"""Search in a turn over POST /v1/messages: the route question `search`, the decider
batch over the candidates (`cand:*` / `kb:*`), the reply context, «I don't know» and the
handover, last_presented for «давайте другий», a scenario field of type catalog.

Indexes are the in-memory fakes; FakeDecider is strict, so every question asked must
have a rule."""

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.pipeline import full_pipeline
from app.core.respond import HANDOVER_RULE
from app.db.models import Conversation, LlmRole, RunStatus, Turn
from app.decisions.fake import FakeDecider
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from app.prompts.seed import seed_prompts
from app.results.service import collected
from app.scenarios.schema import parse_definition
from app.search.presented import STATE_KEY, rewrite_view
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import ROUTE_DEFAULTS, install_scenario, runs_of
from tests.search_fakes import add_item, add_source, fake_search

pytestmark = pytest.mark.db

ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE, LlmRole.EXTRACT)
EMBED = "openai/text-embedding-3-large"
LANGUAGES = {"languages": ["uk", "en"], "reply_language": {"uk": "uk", "ru": "uk", "*": "en"}}
MANICURE = {"sku-1", "sku-2"}


def fits(word: str):
    """FakeDecider rule: the candidate fits when its question names `word`."""
    return lambda question, state: word.lower() in question.instructions.lower()


async def _setup(services, *, settings=None, items=True, knowledge=False):
    fake_search(services)
    decider = FakeDecider({"service_type": "none", "wants_human": False, "search": "none"})
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = decider
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES)
    await make_model(services.sm, EMBED, roles=(LlmRole.EMBED,), kind="embedding")
    bundle = await make_tenant(services.sm, settings={**LANGUAGES, **(settings or {})})
    tid = bundle.tenant_id
    if items:
        await add_item(
            services, tid, "sku-1", "Манікюр класичний", category="manicure",
            price=Decimal("300"), currency="UAH",
        )  # fmt: skip
        await add_item(
            services, tid, "sku-2", "Манікюр з покриттям", category="manicure",
            price=Decimal("450"), currency="UAH", description="Гель-лак.",
        )  # fmt: skip
        await add_item(
            services, tid, "sku-3", "Педикюр", category="pedicure",
            price=Decimal("500"), currency="UAH",
        )  # fmt: skip
    if knowledge:
        await add_source(
            services, tid, "Доставка і оплата", "Відправляємо Новою поштою за 1–2 дні."
        )
    worker = Worker(services.sm, services.clock, HANDLERS, services=services)
    await worker.run_due()  # index everything
    return SimpleNamespace(bundle=bundle, decider=decider, worker=worker)


@pytest.fixture
async def env(services):
    return await _setup(services)


def _rewrite(text: str) -> dict:
    return chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False))


async def _say(client, env, fake_llm, text, rewritten, *, rules=None, extract=None, reply="Ок."):
    env.decider.rules = {
        "service_type": "none",
        "wants_human": False,
        "search": "none",
        **(rules or {}),
    }
    script = [_rewrite(rewritten)]
    if extract is not None:
        script.append(chat_reply(json.dumps(extract, ensure_ascii=False)))
    script.append(chat_reply(reply))
    fake_llm.requests.clear()
    fake_llm.push(*script)
    resp = await client.post(
        "/v1/messages",
        json={"contact": {"external_id": "c1", "name": "Olha"}, "text": text},
        headers=env.bundle.auth,
    )
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    return resp.json()


def _user_json(request: dict) -> dict:
    return json.loads(request["messages"][-1]["content"])


async def _turn(services, turn_id: str) -> Turn:
    async with services.sm() as session:
        return await session.get(Turn, uuid.UUID(turn_id))


async def _conversation(services, conversation_id: str) -> Conversation:
    async with services.sm() as session:
        return await session.get(Conversation, uuid.UUID(conversation_id))


def _step(turn: Turn, name: str) -> dict:
    return next(s for s in turn.trace["steps"] if s["step"] == name)


# --- catalog ----------------------------------------------------------------------------


async def test_found_items_go_into_the_reply_context(client, services, fake_llm, env):
    body = await _say(
        client,
        env,
        fake_llm,
        "скільки манік?",
        "Скільки коштує манікюр?",
        rules={"search": "catalog", "cand:*": fits("манікюр")},
        reply="Манікюр від 300 грн.",
    )
    assert body["reply"] == "Манікюр від 300 грн."
    assert body["handover"] is False

    asked = env.decider.asked()
    assert "search" in asked
    # one decider batch over all the candidates: the two manicures and the pedicure
    assert sorted(k for k in asked if k.startswith("cand:")) == ["cand:1", "cand:2", "cand:3"]

    data = _user_json(fake_llm.requests[-1])
    titles = {c["title"] for c in data["context"]}
    assert titles == {"Манікюр класичний", "Манікюр з покриттям"}
    assert all(c["kind"] == "item" for c in data["context"])
    assert sorted(c["n"] for c in data["context"]) == [1, 2]
    assert data["search_rules"]

    turn = await _turn(services, body["turn_id"])
    assert set(turn.route["search"]["items"]) == MANICURE
    assert turn.route["search"]["not_found"] is False
    step = _step(turn, "search")
    assert step["wanted"] == "catalog"
    assert step["candidates"] == {"catalog": 3, "knowledge": 0}

    # the reply shows these items: «давайте другий» refers to them later
    conv = await _conversation(services, body["conversation_id"])
    shown = conv.state[STATE_KEY]
    assert {i["ref"] for i in shown} == MANICURE
    assert sorted(i["n"] for i in shown) == [1, 2]


async def test_the_search_question_is_skipped_without_content(client, services, fake_llm):
    env = await _setup(services, items=False)
    body = await _say(client, env, fake_llm, "привіт, що є?", "Що у вас є?")
    assert "search" not in env.decider.asked()
    assert "search_rules" not in _user_json(fake_llm.requests[-1])
    turn = await _turn(services, body["turn_id"])
    assert "search" not in turn.route


async def test_no_search_when_the_route_says_none(client, services, fake_llm, env):
    body = await _say(client, env, fake_llm, "дякую", "Дякую")
    assert "search" in env.decider.asked()
    assert not [k for k in env.decider.asked() if k.startswith("cand:")]
    data = _user_json(fake_llm.requests[-1])
    assert data["context"] == []
    assert "search_rules" not in data
    conv = await _conversation(services, body["conversation_id"])
    assert STATE_KEY not in (conv.state or {})


async def test_nothing_fits_leaves_the_context_empty(client, services, fake_llm, env):
    body = await _say(
        client,
        env,
        fake_llm,
        "а стрижка є?",
        "Чи є у вас стрижка?",
        rules={"search": "catalog", "cand:*": False},
        reply="На жаль, такої послуги немає.",
    )
    data = _user_json(fake_llm.requests[-1])
    # the legacy «I don't know» rules of the user prompt answer an empty context
    assert data["context"] == []
    assert data["search_rules"]
    assert "handover_note" not in data
    assert body["handover"] is False
    turn = await _turn(services, body["turn_id"])
    assert turn.route["search"]["not_found"] is True
    conv = await _conversation(services, body["conversation_id"])
    assert STATE_KEY not in (conv.state or {})


async def test_nothing_fits_hands_over_when_the_tenant_wants(client, services, fake_llm):
    env = await _setup(services, settings={"unknown_answer": "handover"})
    body = await _say(
        client,
        env,
        fake_llm,
        "а стрижка є?",
        "Чи є у вас стрижка?",
        rules={"search": "catalog", "cand:*": False},
        reply="Уточню в менеджера.",
    )
    assert body["handover"] is True
    assert _user_json(fake_llm.requests[-1])["handover_note"] == HANDOVER_RULE
    conv = await _conversation(services, body["conversation_id"])
    assert conv.handover is True


async def test_index_down_does_not_break_the_turn(client, services, fake_llm, env):
    from app.search.base import SearchUnavailable

    search = services.extra["search"]
    search.words.fail = SearchUnavailable("connection refused")
    search.vectors.fail = SearchUnavailable("connection refused")
    body = await _say(
        client,
        env,
        fake_llm,
        "скільки манік?",
        "Скільки коштує манікюр?",
        rules={"search": "catalog", "cand:*": True},
        reply="Уточню.",
    )
    assert body["reply"] == "Уточню."
    assert not [k for k in env.decider.asked() if k.startswith("cand:")]
    turn = await _turn(services, body["turn_id"])
    notes = {n["what"] for n in _step(turn, "search")["notes"]}
    assert {"words_error", "vectors_error"} <= notes


# --- knowledge --------------------------------------------------------------------------


async def test_knowledge_answer(client, services, fake_llm):
    env = await _setup(services, items=False, knowledge=True)
    await _say(
        client,
        env,
        fake_llm,
        "як доставляєте?",
        "Як ви доставляєте замовлення?",
        rules={"search": "knowledge", "kb:*": True},
        reply="Новою поштою за 1–2 дні.",
    )
    assert [k for k in env.decider.asked() if k.startswith("kb:")] == ["kb:1"]
    [entry] = _user_json(fake_llm.requests[-1])["context"]
    assert entry == {
        "kind": "knowledge",
        "title": "Доставка і оплата",
        "text": "Доставка і оплата\nВідправляємо Новою поштою за 1–2 дні.",
    }


async def test_both_options_when_the_tenant_has_both(client, services, fake_llm):
    env = await _setup(services, knowledge=True)
    await _say(
        client,
        env,
        fake_llm,
        "скільки манікюр і як доставка?",
        "Скільки коштує манікюр і як доставка?",
        rules={"search": "both", "cand:*": fits("покриттям"), "kb:*": True},
    )
    [question] = [q for _, qs in env.decider.calls for q in qs if q.key == "search"]
    assert set(question.criteria) == {"none", "knowledge", "catalog", "both"}
    context = _user_json(fake_llm.requests[-1])["context"]
    assert [c["kind"] for c in context] == ["item", "knowledge"]
    assert context[0]["title"] == "Манікюр з покриттям"


# --- «давайте другий» -------------------------------------------------------------------


async def test_rewrite_sees_the_options_shown_last(client, services, fake_llm, env):
    first = await _say(
        client,
        env,
        fake_llm,
        "який манікюр є?",
        "Який манікюр у вас є?",
        rules={"search": "catalog", "cand:*": fits("манікюр")},
        reply="1. Манікюр класичний 2. Манікюр з покриттям",
    )
    conv = await _conversation(services, first["conversation_id"])
    shown = conv.state[STATE_KEY]

    await _say(client, env, fake_llm, "давайте другий", f"Хочу {shown[1]['title']}")
    rewrite_input = _user_json(fake_llm.requests[0])
    assert rewrite_input["last_presented_options"] == rewrite_view(shown)
    assert rewrite_input["last_presented_rules"]
    # a turn without new options keeps the list
    conv = await _conversation(services, first["conversation_id"])
    assert conv.state[STATE_KEY] == shown


# --- scenario field of type catalog -----------------------------------------------------

BOOKING = {
    "key": "booking",
    "title": {"uk": "Запис", "en": "Booking"},
    "language": "uk",
    "start": {
        "questions": [
            {
                "key": "wants",
                "ask": {"uk": "Клієнт хоче записатися?", "en": "Does the client want to book?"},
            }
        ]
    },
    "fields": [
        {
            "key": "service",
            "type": "catalog",
            "question": {"uk": "На яку послугу?", "en": "Which service?"},
            "catalog": {"categories": ["manicure"], "max_options": 3},
        },
        {"key": "name", "question": {"uk": "Як вас звати?", "en": "Your name?"}},
    ],
}
SCENARIO_RULES = {**ROUTE_DEFAULTS, "start:booking.wants": True}


@pytest.fixture
async def booking(services):
    env = await _setup(services)
    await install_scenario(services.sm, env.bundle.tenant_id, env.bundle.channel_id, BOOKING)
    return env


def _extract_enum(fake_llm) -> list:
    schema = fake_llm.requests[1]["response_format"]["json_schema"]["schema"]
    return schema["properties"]["booking__service"]["enum"]


async def test_catalog_field_takes_the_named_item(client, services, fake_llm, booking):
    body = await _say(
        client,
        booking,
        fake_llm,
        "хочу на манікюр з покриттям",
        "Хочу записатися на манікюр з покриттям",
        rules={
            **SCENARIO_RULES,
            "field:booking.service": True,
            "search": "catalog",
            "cand:*": fits("покриттям"),
        },
        extract={"booking__service": "sku-2"},
    )
    # the choices are catalog items of the field's categories, never the pedicure
    enum = _extract_enum(fake_llm)
    assert "sku-2" in enum
    assert "sku-3" not in enum
    assert enum[-1] is None

    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.status == RunStatus.FOCUS
    assert run.fields["service"]["state"] == "filled"
    assert run.fields["service"]["value"] == "Манікюр з покриттям"
    assert run.fields["service"]["ref"] == "sku-2"
    assert run.asking == "name"
    # the result carries the external_id next to the title
    defn = parse_definition(BOOKING)
    assert collected(defn, run.fields)["refs"] == {"service": "sku-2"}


async def test_catalog_field_rejects_an_item_not_offered(client, services, fake_llm, booking):
    body = await _say(
        client,
        booking,
        fake_llm,
        "хочу на манікюр",
        "Хочу записатися на манікюр",
        rules={
            **SCENARIO_RULES,
            "field:booking.service": True,
            "search": "catalog",
            "cand:*": fits("манікюр"),
        },
        extract={"booking__service": "sku-3"},
    )
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert not run.fields["service"].get("value")
    turn = await _turn(services, body["turn_id"])
    rejected = json.dumps(_step(turn, "scenarios"), ensure_ascii=False)
    assert "unknown_item" in rejected


async def test_catalog_field_lists_the_options_and_takes_the_second(
    client, services, fake_llm, booking
):
    # 1) the client wants to book: the bot lists the manicures (≤ max_options)
    body = await _say(
        client,
        booking,
        fake_llm,
        "хочу записатися",
        "Хочу записатися",
        rules=SCENARIO_RULES,
        # a run started in this turn looks for all its fields in the message
        extract={"booking__service": None, "booking__name": None},
    )
    step = json.loads(fake_llm.requests[-1]["messages"][-1]["content"])["scenario_step"]
    options = step["ask_next"]["options"]
    assert len(options) == 2
    assert options[0].startswith("1. Манікюр")
    conv = await _conversation(services, body["conversation_id"])
    shown = conv.state[STATE_KEY]
    assert {i["ref"] for i in shown} == MANICURE
    second = next(i for i in shown if i["n"] == 2)

    # 2) «давайте другий»: the rewrite gets the options, the extract picks option 2
    body = await _say(
        client,
        booking,
        fake_llm,
        "давайте другий",
        f"Хочу записатися на {second['title']}",
        rules={**SCENARIO_RULES, "field:booking.service": True},
        extract={"booking__service": second["ref"]},
    )
    assert _user_json(fake_llm.requests[0])["last_presented_options"] == rewrite_view(shown)
    assert set(_extract_enum(fake_llm)) >= {*MANICURE, None}
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.fields["service"]["ref"] == second["ref"]
    assert run.fields["service"]["value"] == second["title"]


# --- a catalog too long to list ---------------------------------------------------------

# the whole catalog (3 items, 2 categories) with at most 2 options in a message
LONG = {
    **BOOKING,
    "fields": [
        {**BOOKING["fields"][0], "catalog": {"categories": [], "max_options": 2}},
        BOOKING["fields"][1],
    ],
}
NOTHING_NAMED = {"booking__service": None, "booking__name": None}


@pytest.fixture
async def long_catalog(services):
    env = await _setup(services)
    await install_scenario(services.sm, env.bundle.tenant_id, env.bundle.channel_id, LONG)
    return env


def _ask_next(fake_llm) -> dict:
    return _user_json(fake_llm.requests[-1])["scenario_step"]["ask_next"]


async def _catalog_ask(services, body: dict) -> dict:
    turn = await _turn(services, body["turn_id"])
    return _step(turn, "scenarios")["summary"]["catalog_ask"]["booking.service"]


async def test_named_items_are_listed_without_the_search_step(
    client, services, fake_llm, long_catalog
):
    # the route sees no question (search = none), the extract cannot choose between the
    # two manicures: the bot lists exactly them, with prices
    body = await _say(
        client,
        long_catalog,
        fake_llm,
        "хочу на манікюр",
        "Хочу на манікюр",
        rules={
            **SCENARIO_RULES,
            "field:booking.service": True,
            "opt:*": fits("манікюр"),
        },
        extract=NOTHING_NAMED,
    )
    ask = _ask_next(fake_llm)
    assert sorted(o.split(". ", 1)[1] for o in ask["options"]) == [
        "Манікюр з покриттям — 450.00 UAH",
        "Манікюр класичний — 300.00 UAH",
    ]
    assert ask["from_catalog"] is True
    assert "categories" not in ask
    assert "more_options" not in ask
    shown = (await _conversation(services, body["conversation_id"])).state[STATE_KEY]
    assert {i["ref"] for i in shown} == MANICURE
    trace = await _catalog_ask(services, body)
    assert trace["source"] == "named"
    assert set(trace["items"]) == MANICURE
    # one search for the extract choices and the options
    assert len(services.extra["search"].words.searches) == 1

    # «давайте другий» takes the second of the listed
    second = next(i for i in shown if i["n"] == 2)
    body = await _say(
        client,
        long_catalog,
        fake_llm,
        "давайте другий",
        f"Хочу записатися на {second['title']}",
        rules={**SCENARIO_RULES, "field:booking.service": True},
        extract={"booking__service": second["ref"]},
    )
    [run] = await runs_of(services.sm, body["conversation_id"])
    assert run.fields["service"]["ref"] == second["ref"]


async def test_nothing_named_asks_by_the_categories(client, services, fake_llm, long_catalog):
    body = await _say(
        client,
        long_catalog,
        fake_llm,
        "хочу записатися",
        "Хочу записатися",
        rules={**SCENARIO_RULES, "opt:*": False},
        extract=NOTHING_NAMED,
    )
    ask = _ask_next(fake_llm)
    assert ask == {
        "question": "На яку послугу?",
        "from_catalog": True,
        "categories": ["manicure", "pedicure"],
    }
    conv = await _conversation(services, body["conversation_id"])
    assert STATE_KEY not in (conv.state or {})
    assert (await _catalog_ask(services, body))["source"] == "categories"

    # the client picks a category: its items are listed
    body = await _say(
        client,
        long_catalog,
        fake_llm,
        "манікюр",
        "Манікюр",
        rules={**SCENARIO_RULES, "field:booking.service": True, "opt:*": fits("manicure")},
        extract={"booking__service": None},
    )
    ask = _ask_next(fake_llm)
    assert len(ask["options"]) == 2
    assert all("Манікюр" in o for o in ask["options"])
    assert "categories" not in ask


async def test_more_items_fit_than_can_be_listed(client, services, fake_llm, long_catalog):
    body = await _say(
        client,
        long_catalog,
        fake_llm,
        "щось для рук чи ніг",
        "Послуга для рук чи ніг",
        rules={**SCENARIO_RULES, "field:booking.service": True, "opt:*": True},
        extract=NOTHING_NAMED,
    )
    ask = _ask_next(fake_llm)
    assert len(ask["options"]) == 2
    assert ask["more_options"] is True
    trace = await _catalog_ask(services, body)
    assert trace["more"] is True
    assert len(trace["items"]) == 2


async def test_categories_keep_the_order_of_the_field(client, services, fake_llm):
    env = await _setup(services)
    ordered = {
        **LONG,
        "fields": [
            {
                **LONG["fields"][0],
                "catalog": {"categories": ["pedicure", "manicure"], "max_options": 2},
            },
            LONG["fields"][1],
        ],
    }
    await install_scenario(services.sm, env.bundle.tenant_id, env.bundle.channel_id, ordered)
    await _say(
        client,
        env,
        fake_llm,
        "хочу записатися",
        "Хочу записатися",
        rules={**SCENARIO_RULES, "opt:*": False},
        extract=NOTHING_NAMED,
    )
    assert _ask_next(fake_llm)["categories"] == ["pedicure", "manicure"]


async def test_the_search_step_items_are_listed_as_before(client, services, fake_llm, long_catalog):
    # the route asked for the catalog: its checked items are the options, no second check
    body = await _say(
        client,
        long_catalog,
        fake_llm,
        "хочу на манікюр",
        "Хочу записатися на манікюр",
        rules={
            **SCENARIO_RULES,
            "field:booking.service": True,
            "search": "catalog",
            "cand:*": fits("манікюр"),
        },
        extract=NOTHING_NAMED,
    )
    assert len(_ask_next(fake_llm)["options"]) == 2
    assert (await _catalog_ask(services, body))["source"] == "search"


async def test_the_reply_rules_keep_to_the_catalog(client, services, fake_llm, long_catalog):
    await _say(
        client,
        long_catalog,
        fake_llm,
        "хочу записатися",
        "Хочу записатися",
        rules={**SCENARIO_RULES, "opt:*": False},
        extract=NOTHING_NAMED,
    )
    rules = " ".join(_user_json(fake_llm.requests[-1])["scenario_step"]["rules"])
    assert "'ask_next.from_catalog'" in rules
    assert "'client_description'" in rules
    assert "'ask_next.categories'" in rules
    assert "'ask_next.more_options'" in rules
