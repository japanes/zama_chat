"""Ready scenarios the admin offers to start a new one from."""

import yaml

from app.scenarios.schema import check_definition, parse_definition
from app.scenarios.templates import list_templates
from app.settings import PROJECT_ROOT
from tests.scenario_helpers import interview_data

LANGUAGES = ("uk", "en", "ru")


def test_the_project_templates_are_offered_with_human_names():
    templates = list_templates(PROJECT_ROOT / "scenarios")
    assert {t["key"] for t in templates} >= {
        "beauty_booking",
        "cancel_booking",
        "group_booking",
        "offer_interview",
        "prepaid_order",
        "reschedule_booking",
    }
    for t in templates:
        # the card of a template: its name and what it is for, in the admin's languages
        assert set(t["title"]) >= set(LANGUAGES), t["key"]
        assert set(t["description"]) >= set(LANGUAGES), t["key"]
        assert t["fields"], t["key"]
        for f in t["fields"]:
            # a question is shown by its name, not by its key
            assert f["title"] and set(f["title"]) >= set(LANGUAGES), (t["key"], f["key"])
        # the draft the editor gets is a scenario that can be saved as it is
        defn = parse_definition(t["definition"])
        assert [f.key for f in defn.fields] == [f["key"] for f in t["fields"]]
        check_definition(defn, ["uk", "en"])


def test_a_broken_file_is_skipped_the_others_stay(tmp_path):
    (tmp_path / "a_good.yaml").write_text(
        yaml.safe_dump(interview_data(), allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "b_no_fields.yaml").write_text("key: broken\ntitle: Broken\n", encoding="utf-8")
    (tmp_path / "c_not_yaml.yaml").write_text("key: [unclosed", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a scenario", encoding="utf-8")

    templates = list_templates(tmp_path)
    assert [t["key"] for t in templates] == ["interview"]
    assert templates[0]["definition"]["key"] == "interview"


def test_no_folder_no_templates(tmp_path):
    assert list_templates(tmp_path / "missing") == []
