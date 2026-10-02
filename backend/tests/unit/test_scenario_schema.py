from pathlib import Path

import pytest

from app.errors import AppError
from app.scenarios.loader import parse_yaml, prepare, read_file
from app.scenarios.schema import check_definition, parse_definition, pick_text
from app.settings import PROJECT_ROOT
from tests.scenario_helpers import booking_data, interview_data, make_def

SCENARIOS_DIR = PROJECT_ROOT / "scenarios"


def _code(data, languages=("uk", "en")) -> str:
    with pytest.raises(AppError) as exc:
        check_definition(parse_definition(data), list(languages))
    return exc.value.code


def test_the_offer_interview_file_is_valid_without_warnings():
    defn, warnings = prepare(read_file(SCENARIOS_DIR / "offer_interview.yaml"), ["uk", "en"])
    assert defn.key == "offer_interview"
    assert defn.field_keys == ["niche", "audience", "main_offer", "price", "positioning"]
    assert defn.field("positioning").ask_after == ["niche", "main_offer"]
    assert defn.threshold == 2
    assert [a.on for a in defn.actions] == ["completed", "silence", "cancelled"]
    assert warnings == []


def test_every_scenario_file_loads():
    files = sorted(Path(SCENARIOS_DIR).glob("*.yaml"))
    assert files
    for path in files:
        prepare(read_file(path), ["uk", "en"])


def test_yaml_keeps_on_yes_no_as_strings():
    assert parse_yaml("on: completed\nyes: Так\nno: Ні\nflag: true\noff: x") == {
        "on": "completed",
        "yes": "Так",
        "no": "Ні",
        "flag": True,
        "off": "x",
    }


def test_a_string_and_a_dict_read_the_same():
    assert (
        pick_text("Яка ніша?", "uk", "uk").text == pick_text({"uk": "Яка ніша?"}, "uk", "uk").text
    )


def test_pick_text_falls_back_to_the_main_language():
    picked = pick_text({"uk": "Яка ніша?", "en": "Niche?"}, "en", "uk")
    assert (picked.text, picked.language, picked.exact) == ("Niche?", "en", True)
    picked = pick_text({"uk": "Яка ніша?"}, "en", "uk")
    assert (picked.text, picked.language, picked.exact) == ("Яка ніша?", "uk", False)
    picked = pick_text("Яка ніша?", "en", "uk")
    assert (picked.language, picked.exact) == ("uk", False)


def test_a_text_missing_in_a_tenant_language_is_a_warning():
    data = interview_data()
    data["fields"][1]["question"] = "Що ви продаєте?"  # main language only
    warnings = check_definition(parse_definition(data), ["uk", "en"])
    assert warnings == [
        {
            "code": "scenario.text_missing_language",
            "params": {"path": "fields.offer.question", "language": "en"},
        }
    ]


def test_single_start_question_threshold_is_its_weight():
    assert make_def(booking_data()).threshold == 1


def test_threshold_higher_than_all_weights_is_an_error():
    data = interview_data()
    data["start"]["threshold"] = 5
    with pytest.raises(AppError) as exc:
        check_definition(parse_definition(data), ["uk", "en"])
    assert exc.value.code == "scenario.threshold_too_high"
    assert exc.value.params == {"threshold": 5, "max": 4}


def test_several_start_questions_need_a_threshold():
    data = interview_data()
    del data["start"]["threshold"]
    assert _code(data) == "scenario.threshold_required"


def test_start_question_on_an_unknown_field():
    data = interview_data()
    data["start"]["questions"][1] = {"field": "nope"}
    assert _code(data) == "scenario.start_field_unknown"


def test_ask_after_unknown_and_cycle():
    data = interview_data()
    data["fields"][0]["ask_after"] = ["nope"]
    assert _code(data) == "scenario.ask_after_unknown"

    data = interview_data()
    data["fields"][0]["ask_after"] = ["positioning"]
    assert _code(data) == "scenario.ask_after_cycle"


def test_choice_needs_options():
    data = interview_data()
    data["fields"][1]["type"] = "choice"
    assert _code(data) == "scenario.choice_options_missing"


def test_http_post_needs_an_endpoint():
    data = interview_data(actions=[{"on": "completed", "do": "http_post"}])
    assert _code(data) == "scenario.action_endpoint_required"


def test_after_minutes_only_for_silence():
    data = interview_data(
        actions=[{"on": "completed", "do": "http_post", "endpoint": "x", "after_minutes": 5}]
    )
    assert _code(data) == "scenario.after_minutes_not_silence"


def test_unknown_action():
    data = interview_data(actions=[{"on": "completed", "do": "launch_rocket"}])
    assert _code(data) == "scenario.action_unknown"


def test_within_hours_needs_hours():
    data = interview_data(resume={"mode": "within_hours"})
    assert _code(data) == "scenario.resume_hours_required"


def test_format_errors_have_locations():
    data = interview_data()
    data["fields"][0]["key"] = "Bad Key"
    with pytest.raises(AppError) as exc:
        parse_definition(data)
    assert exc.value.code == "scenario.invalid"
    assert any(e["loc"].startswith("fields.0.key") for e in exc.value.params["errors"])


def test_keys_cannot_contain_a_double_underscore():
    data = interview_data()
    data["fields"][0]["key"] = "my__niche"
    with pytest.raises(AppError):
        parse_definition(data)


# --- stage 4 actions ----------------------------------------------------------------------


def _with_action(action: dict) -> dict:
    data = interview_data()
    data["actions"] = [action]
    return data


@pytest.mark.parametrize(
    ("action", "code"),
    [
        ({"on": "completed", "do": "notify_telegram"}, "scenario.action_notifier_required"),
        (
            {"on": "completed", "do": "notify_email", "params": {"notifier": "Bad Name"}},
            "scenario.action_notifier_required",
        ),
        ({"on": "completed", "do": "message_client"}, "scenario.action_text_required"),
        (
            {"on": "completed", "do": "message_client", "params": {"text": "  "}},
            "scenario.action_text_required",
        ),
        (
            {"on": "completed", "do": "message_client", "params": {"text": {"uk": ""}}},
            "scenario.action_text_required",
        ),
        ({"on": "completed", "do": "send_fax"}, "scenario.action_unknown"),
    ],
)
def test_bad_stage4_actions(action, code):
    assert _code(_with_action(action)) == code


def test_good_stage4_actions():
    data = interview_data()
    data["actions"] = [
        {"on": "completed", "do": "notify_telegram", "params": {"notifier": "team"}},
        {"on": "field_stuck", "do": "notify_email", "params": {"notifier": "sales-1"}},
        {"on": "field_stuck", "do": "handover"},
        {
            "on": "completed",
            "do": "message_client",
            "params": {"text": {"uk": "Дякуємо!", "en": "Thank you!"}},
        },
    ]
    assert check_definition(parse_definition(data), ["uk", "en"]) == []


def test_message_client_text_missing_a_language_is_a_warning():
    action = {"on": "completed", "do": "message_client", "params": {"text": "Дякуємо!"}}
    warnings = check_definition(parse_definition(_with_action(action)), ["uk", "en"])
    assert warnings == [
        {
            "code": "scenario.text_missing_language",
            "params": {"path": "actions.0.text", "language": "en"},
        }
    ]
