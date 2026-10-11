"""The description of the scenario file format (scenarios/README.md) says what the code
takes: every name of the format is in it, and every example in it is a file that loads."""

import re
import types
from enum import StrEnum
from typing import get_args, get_origin

from pydantic import BaseModel

from app.scenarios.format import read_format
from app.scenarios.loader import SUFFIXES, parse_yaml, prepare
from app.scenarios.schema import KNOWN_ACTIONS, ScenarioDef, check_definition, parse_definition
from app.settings import PROJECT_ROOT

FOLDER = PROJECT_ROOT / "scenarios"
YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _doc() -> dict[str, str]:
    found = read_format(FOLDER)
    assert found is not None
    return found


def _names(model: type[BaseModel], seen: set[type]) -> set[str]:
    """Everything a person can write in a file: the keys of the format and the values
    it lets choose from."""
    if model in seen:
        return set()
    seen.add(model)
    out = set(model.model_fields)
    for info in model.model_fields.values():
        todo = [info.annotation]
        while todo:
            kind = todo.pop()
            if get_origin(kind) is not None:
                # Literal["always", ...] gives its values, the rest gives its parts
                todo.extend(a for a in get_args(kind) if not isinstance(a, str))
                out.update(a for a in get_args(kind) if isinstance(a, str))
            elif isinstance(kind, type) and issubclass(kind, BaseModel):
                out |= _names(kind, seen)
            elif isinstance(kind, type) and issubclass(kind, StrEnum):
                out.update(str(v) for v in kind)
            elif isinstance(kind, types.UnionType):
                todo.extend(get_args(kind))
    return out


def test_every_name_of_the_format_is_described():
    text = _doc()["text"]
    names = _names(ScenarioDef, set()) | set(KNOWN_ACTIONS)
    # a sanity check of the walk itself: nested models and enums are reached
    assert {"max_days_ahead", "within_hours", "field_stuck", "receipt", "reopen"} <= names
    # as a whole word: in the text or in an example
    missing = sorted(n for n in names if not re.search(rf"(?<![a-z_]){n}(?![a-z_])", text))
    assert missing == [], f"not described in scenarios/README.md: {missing}"


def test_the_file_suffixes_are_described():
    text = _doc()["text"]
    assert all(f"`{suffix}`" in text for suffix in SUFFIXES)


def test_the_example_file_loads_without_a_remark():
    example = _doc()["example"]
    assert example.startswith("#") and example.endswith("\n")
    defn, warnings = prepare(parse_yaml(example), ["ru"])
    assert defn.key == "consultation_request"
    assert warnings == []
    # every kind of text: a plain string and a text per language
    assert isinstance(defn.title, dict) and isinstance(defn.fields[1].question, str)


def test_every_example_of_the_description_is_written_correctly():
    blocks = YAML_BLOCK.findall(_doc()["text"])
    assert len(blocks) > 15
    whole = 0
    for block in blocks:
        data = parse_yaml(block)
        assert data, block
        # a whole file is checked the way an uploaded one is
        if isinstance(data, dict) and {"key", "start", "fields"} <= set(data):
            check_definition(parse_definition(data), [])
            whole += 1
    assert whole == 2  # the smallest file and the complete example


def test_no_description_in_a_folder_without_one(tmp_path):
    assert read_format(tmp_path) is None
    (tmp_path / "README.md").write_text("# no example here\n", encoding="utf-8")
    assert read_format(tmp_path) == {"text": "# no example here\n", "example": ""}
