"""A scenario draft from a description in words: the model's sketch → a definition."""

import pytest

from app.errors import AppError
from app.prompts.builtin import SCENARIO_DRAFT_KEY, builtin_prompts
from app.prompts.store import PromptText
from app.scenarios.draft import (
    MAX_QUESTIONS,
    build_definition,
    draft_messages,
    draft_schema,
    read_sketch,
)
from app.scenarios.loader import prepare

LANGS = ["uk", "en"]


def _t(uk: str, en: str) -> dict[str, str]:
    return {"uk": uk, "en": en}


def _sketch(**changes) -> dict:
    sketch = {
        "key": "haircut_booking",
        "title": _t("Запис на стрижку", "Haircut booking"),
        "description": _t("Бот збирає заявку на стрижку.", "The bot takes a haircut request."),
        "start": [_t("Клієнт хоче записатися на стрижку?", "Does the client want a haircut?")],
        "questions": [
            {
                "key": "service",
                "name": _t("Послуга", "Service"),
                "type": "choice",
                "question": _t("Яка стрижка потрібна?", "Which haircut do you need?"),
                "options": [_t("Жіноча", "Women's"), _t("Чоловіча", "Men's")],
            },
            {
                "key": "day",
                "name": _t("День", "Day"),
                "type": "date",
                "question": _t("На який день?", "Which day?"),
                "options": [],
            },
            {
                "key": "phone",
                "name": _t("Телефон", "Phone"),
                "type": "phone",
                "question": _t("Ваш номер телефону?", "Your phone number?"),
                "options": [],
            },
        ],
        "confirm": True,
    }
    sketch.update(changes)
    return sketch


def test_a_sketch_becomes_a_scenario_that_passes_the_check():
    data = build_definition(_sketch(), LANGS)
    defn, warnings = prepare(data, LANGS)
    assert warnings == []
    assert (defn.key, defn.language) == ("haircut_booking", "uk")
    assert defn.title == _t("Запис на стрижку", "Haircut booking")
    assert [(f.key, f.type) for f in defn.fields] == [
        ("service", "choice"),
        ("day", "date"),
        ("phone", "phone"),
    ]
    assert defn.fields[0].title == _t("Послуга", "Service")
    assert [(o.key, o.label["uk"]) for o in defn.fields[0].options] == [
        ("option", "Жіноча"),
        ("option_2", "Чоловіча"),
    ]
    # each sign alone starts the scenario
    assert [(q.key, q.weight) for q in defn.start.questions] == [("sign", 1)]
    assert defn.threshold == 1
    assert defn.confirm is not None


def test_keys_are_made_valid_and_different():
    questions = [
        {"key": "Phone Number!", "name": "Телефон", "type": "phone", "question": "Номер?"},
        {"key": "phone_number", "name": "Ще телефон", "type": "phone", "question": "Ще номер?"},
        {"key": "Телефон", "name": "Третій", "type": "text", "question": "Третій номер?"},
        {"key": "9 lives", "name": "Четвертий", "type": "text", "question": "Четвертий?"},
        {"name": "П'ятий", "type": "text", "question": "П'ятий?"},
    ]
    data = build_definition(_sketch(key="Запис 2024", questions=questions), LANGS)
    assert data["key"] == "scenario"
    assert [f["key"] for f in data["fields"]] == [
        "phone_number",
        "phone_number_2",
        "question",
        "question_2",
        "question_3",
    ]
    prepare(data, LANGS)


def test_what_can_not_be_used_is_left_out():
    questions = [
        # a choice with nothing to choose from → the client answers in words
        {"key": "color", "name": "Колір", "type": "choice", "question": "Колір?", "options": ["Є"]},
        # options only make sense for a choice
        {"key": "day", "name": "День", "type": "date", "question": "День?", "options": ["a", "b"]},
        # a type a description is not enough for
        {"key": "item", "name": "Товар", "type": "catalog", "question": "Який товар?"},
        # no name: the question still stands
        {"key": "note", "type": "text", "question": {"uk": "  Побажання?  ", "de": "Wünsche?"}},
        # only a name: it is the question too
        {"key": "name", "name": _t("Ім'я", "Name")},
        # nothing to ask
        {"key": "empty", "name": "", "question": {"uk": " "}},
        "not a question",
    ]
    data = build_definition(_sketch(questions=questions, confirm=False, description=None), LANGS)
    by_key = {f["key"]: f for f in data["fields"]}
    assert list(by_key) == ["color", "day", "item", "note", "name"]
    assert by_key["color"]["type"] == "text" and "options" not in by_key["color"]
    assert by_key["day"]["type"] == "date" and "options" not in by_key["day"]
    assert by_key["item"]["type"] == "text"
    # a plain string is the text in the main language; a language the tenant has not is dropped
    assert by_key["note"] == {"key": "note", "type": "text", "question": {"uk": "Побажання?"}}
    assert by_key["name"]["question"] == _t("Ім'я", "Name")
    assert "confirm" not in data and "description" not in data
    defn, warnings = prepare(data, LANGS)
    # texts written in one language only are the person's to finish: a warning, not an error
    assert {w["code"] for w in warnings} == {"scenario.text_missing_language"}
    assert defn.confirm is None


def test_without_signs_the_first_question_starts_the_scenario():
    data = build_definition(_sketch(start=[{"uk": ""}, 5], title=None), LANGS)
    assert data["start"] == {"questions": [{"field": "service", "weight": 1}], "threshold": 1}
    # a draft without a name is called by its first question
    assert data["title"] == _t("Послуга", "Service")
    prepare(data, LANGS)


def test_several_signs_and_a_long_list_of_questions():
    question = {"key": "q", "name": "Питання", "type": "text", "question": "Питання?"}
    sketch = _sketch(start=["Перша?", "Друга?", "Третя?"], questions=[question] * 30)
    data = build_definition(sketch, LANGS)
    assert [q["key"] for q in data["start"]["questions"]] == ["sign", "sign_2", "sign_3"]
    assert len(data["fields"]) == MAX_QUESTIONS
    assert len({f["key"] for f in data["fields"]}) == MAX_QUESTIONS
    prepare(data, LANGS)


@pytest.mark.parametrize("questions", [[], None, "many", [{"key": "a"}]])
def test_no_questions_no_draft(questions):
    with pytest.raises(AppError) as exc:
        build_definition(_sketch(questions=questions), LANGS)
    assert exc.value.code == "scenario.draft_empty"
    assert exc.value.status == 422


def test_the_schema_asks_every_text_in_each_language():
    schema = draft_schema(["uk", "ru", "en"])
    title = schema["properties"]["title"]
    assert title["required"] == ["uk", "ru", "en"] and title["additionalProperties"] is False
    question = schema["properties"]["questions"]["items"]
    assert question["properties"]["question"] == title
    assert "catalog" not in question["properties"]["type"]["enum"]
    # strict structured output: every property is required, nothing extra
    for node in (schema, question):
        assert node["required"] == list(node["properties"])
        assert node["additionalProperties"] is False


def test_the_prompt_carries_the_description_and_the_languages():
    prompt = PromptText(SCENARIO_DRAFT_KEY, 1, builtin_prompts()[SCENARIO_DRAFT_KEY])
    [message] = draft_messages(prompt, "Клієнт записується на стрижку.", LANGS)
    assert message["role"] == "user"
    assert "Клієнт записується на стрижку." in message["content"]
    assert '"uk"' in message["content"] and '"en"' in message["content"]


def test_a_sketch_is_read_from_an_answer_in_text():
    assert read_sketch('Here it is:\n```json\n{"key": "a", "questions": []}\n```\nDone.') == {
        "key": "a",
        "questions": [],
    }
    assert read_sketch("I can not help with that.") is None
    assert read_sketch("{broken") is None
    assert read_sketch("[1, 2] {oops}") is None
