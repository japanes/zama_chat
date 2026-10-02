import json
import uuid
from types import SimpleNamespace

from app.core.context import HistoryItem
from app.db.models import LlmRole, MessageRole
from app.scenarios.extract import (
    DEFAULT_PROMPT,
    Target,
    extract,
    extract_messages,
    extract_schema,
    field_spec,
    grounded,
    parse_values,
    prop_name,
    words,
)
from tests.scenario_helpers import interview_data, make_def
from tests.turn_ctx import T0, make_ctx

DEFN = make_def(
    interview_data(
        fields=[
            {"key": "niche", "question": {"uk": "Яка ніша?", "en": "Niche?"}},
            {
                "key": "format",
                "type": "choice",
                "question": {"uk": "Формат?", "en": "Format?"},
                "options": [
                    {"key": "online", "label": {"uk": "Онлайн", "en": "Online"}},
                    {"key": "offline", "label": {"uk": "Офлайн", "en": "Offline"}},
                ],
            },
            {"key": "has_site", "type": "yes_no", "question": {"uk": "Є сайт?", "en": "Site?"}},
        ],
        start={"questions": [{"key": "go", "ask": "Go?"}]},
    )
)
RUN = uuid.uuid4()
NICHE = Target(RUN, DEFN, DEFN.field("niche"))
FORMAT = Target(RUN, DEFN, DEFN.field("format"))
HAS_SITE = Target(RUN, DEFN, DEFN.field("has_site"))


def test_property_names_join_scenario_and_field():
    assert prop_name("interview", "niche") == "interview__niche"
    assert NICHE.prop == "interview__niche"


def test_schema_has_every_target_required_and_enums_for_choices():
    schema = extract_schema([NICHE, FORMAT, HAS_SITE])
    assert schema["required"] == ["interview__niche", "interview__format", "interview__has_site"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["interview__niche"] == {"type": ["string", "null"]}
    assert schema["properties"]["interview__format"]["enum"] == ["online", "offline", None]
    assert schema["properties"]["interview__has_site"]["enum"] == ["yes", "no", None]


def test_field_spec_in_the_reply_language_with_options_and_current_value():
    spec = field_spec(Target(RUN, DEFN, DEFN.field("format"), current="онлайн"), "en")
    assert spec == {
        "id": "interview__format",
        "type": "choice",
        "question": "Format?",
        "options": [{"key": "online", "label": "Online"}, {"key": "offline", "label": "Offline"}],
        "current_value": "онлайн",
    }


def test_messages_carry_the_original_text_and_the_last_bot_question():
    ctx = make_ctx(
        input_text="Я печу торти на замовлення",
        history=[
            HistoryItem(MessageRole.BOT, "Яка у вас ніша?", T0),
            HistoryItem(MessageRole.CLIENT, "Я печу торти на замовлення", T0),
        ],
    )
    messages = extract_messages(DEFAULT_PROMPT, [NICHE], ctx, "Клієнт пече торти", "uk")
    assert len(messages) == 1 and messages[0]["role"] == "user"
    data = json.loads(messages[0]["content"])
    assert data["client_message"] == "Я печу торти на замовлення"
    assert data["rewritten"] == "Клієнт пече торти"
    assert data["bot_question"] == "Яка у вас ніша?"
    assert data["fields"][0]["id"] == "interview__niche"
    assert data["rules"]  # the builtin rules are kept


def test_words_split_on_punctuation_and_lowercase():
    assert words("Торти, кекси — 500грн!") == ["торти", "кекси", "500грн"]


def test_grounded_accepts_the_client_words_with_other_endings():
    source = "Я роблю манікюр і педикюр у своєму салоні"
    assert grounded("манікюру та педикюру", source)
    assert grounded("салон", source)


def test_grounded_rejects_a_retelling():
    source = "Я роблю нігті"
    assert not grounded("послуги нейл-майстра в салоні краси", source)


def test_grounded_numbers_must_match_exactly():
    assert grounded("500", "від 500 грн")
    assert not grounded("600", "від 500 грн")


def test_grounded_empty_value_is_not_grounded():
    assert not grounded("...", "що завгодно")


def test_parse_values_keeps_grounded_and_valid_values():
    data = {
        "interview__niche": "торти на замовлення",
        "interview__format": "online",
        "interview__has_site": "no",
    }
    values, rejected = parse_values(data, [NICHE, FORMAT, HAS_SITE], "Я печу торти на замовлення")
    assert values == {
        ("interview", "niche"): "торти на замовлення",
        ("interview", "format"): "online",
        ("interview", "has_site"): "no",
    }
    assert rejected == []


def test_parse_values_rejects_with_reasons():
    data = {
        "interview__niche": "кондитерський бізнес преміум-класу",
        "interview__format": "hybrid",
        "interview__has_site": "maybe",
    }
    values, rejected = parse_values(data, [NICHE, FORMAT, HAS_SITE], "Я печу торти")
    assert values == {}
    assert [r["reason"] for r in rejected] == ["not_in_message", "unknown_option", "not_yes_no"]
    assert rejected[0]["field"] == "interview.niche"


def test_parse_values_skips_null_and_blank():
    data = {"interview__niche": None, "interview__format": "  "}
    assert parse_values(data, [NICHE, FORMAT], "x") == ({}, [])
    assert parse_values(None, [NICHE], "x") == ({}, [])


class RecordingLLM:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def complete(self, role, messages, **kw):
        self.calls.append((role, messages, kw))
        return SimpleNamespace(data=self.data, call_id="call-1", model="openai/a")


async def test_extract_calls_the_extract_role_with_the_schema():
    llm = RecordingLLM({"interview__niche": "торти"})
    ctx = make_ctx(input_text="торти")
    out = await extract(
        SimpleNamespace(llm=llm), ctx, None, [NICHE], rewritten="Торти", language="uk"
    )
    role, _, kw = llm.calls[0]
    assert role == LlmRole.EXTRACT
    assert kw["schema_name"] == "extract"
    assert kw["schema"]["required"] == ["interview__niche"]
    assert kw["prompt_key"] == "extract"
    assert out.values == {("interview", "niche"): "торти"}
    assert (out.llm_call_id, out.model) == ("call-1", "openai/a")


async def test_extract_without_targets_does_not_call_the_llm():
    llm = RecordingLLM({})
    out = await extract(SimpleNamespace(llm=llm), make_ctx(), None, [], rewritten="", language="uk")
    assert out.values == {}
    assert llm.calls == []
