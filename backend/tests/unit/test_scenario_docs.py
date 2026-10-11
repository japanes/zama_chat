"""The documentation of the scenario file (admin/docs/<language>/scenario-file.md, shown
in the admin at /docs/scenario-file) says what the code takes, in every language of the
interface: every name of the format is in it, and every example in it is a file that loads."""

import re
import types
from enum import StrEnum
from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel

from app.scenarios.loader import SUFFIXES, parse_yaml, prepare
from app.scenarios.schema import KNOWN_ACTIONS, ScenarioDef, check_definition, parse_definition
from app.settings import PROJECT_ROOT
from tests.scenario_helpers import DOCS_DIR, scenario_doc, scenario_doc_example

# the languages of the admin's interface
LANGUAGES = sorted(
    p.name for p in (PROJECT_ROOT / "admin" / "i18n" / "locales").iterdir() if p.is_dir()
)
YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
HEADING = re.compile(r"^(#{1,3}) ", re.MULTILINE)
TABLE_ROW = re.compile(r"^\|", re.MULTILINE)

in_every_language = pytest.mark.parametrize("language", LANGUAGES)


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


def _shape(value: Any) -> Any:
    """What an example is made of, without its words: the names and the kinds of values.
    A text per language ({ru: …, uk: …}) is a text, as a plain string is."""
    if isinstance(value, dict):
        if value and all(re.fullmatch(r"[a-z]{2,3}", str(k)) for k in value):
            return "str"
        return {str(k): _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return type(value).__name__


def test_the_documentation_is_written_in_every_language_of_the_interface():
    assert len(LANGUAGES) >= 3
    assert sorted(p.name for p in DOCS_DIR.iterdir()) == LANGUAGES
    pages = {
        language: sorted(p.name for p in (DOCS_DIR / language).iterdir()) for language in LANGUAGES
    }
    assert all(names == pages[LANGUAGES[0]] for names in pages.values()), pages
    assert "scenario-file.md" in pages[LANGUAGES[0]]


def test_every_language_says_the_same():
    # the same sections, tables and examples: a part added in one language is added in all
    first, *others = LANGUAGES
    text = scenario_doc(first)
    for language in others:
        other = scenario_doc(language)
        assert HEADING.findall(other) == HEADING.findall(text), language
        assert len(TABLE_ROW.findall(other)) == len(TABLE_ROW.findall(text)), language
        blocks, same = YAML_BLOCK.findall(other), YAML_BLOCK.findall(text)
        assert len(blocks) == len(same), language
        for block, origin in zip(blocks, same, strict=True):
            assert _shape(parse_yaml(block)) == _shape(parse_yaml(origin)), (language, block)


@in_every_language
def test_every_name_of_the_format_is_described(language):
    text = scenario_doc(language)
    names = _names(ScenarioDef, set()) | set(KNOWN_ACTIONS)
    # a sanity check of the walk itself: nested models and enums are reached
    assert {"max_days_ahead", "within_hours", "field_stuck", "receipt", "reopen"} <= names
    # as a whole word: in the text or in an example
    missing = sorted(n for n in names if not re.search(rf"(?<![a-z_]){n}(?![a-z_])", text))
    assert missing == [], f"not described in admin/docs/{language}/scenario-file.md: {missing}"


@in_every_language
def test_the_file_suffixes_are_described(language):
    text = scenario_doc(language)
    assert all(f"`{suffix}`" in text for suffix in SUFFIXES)


@in_every_language
def test_the_example_file_loads_without_a_remark(language):
    example = scenario_doc_example(language)
    assert example.startswith("#") and example.endswith("\n")
    # written in the language of its documentation
    defn, warnings = prepare(parse_yaml(example), [language])
    assert defn.key == "consultation_request" and defn.language == language
    assert warnings == []
    # every kind of text: a plain string and a text per language
    assert isinstance(defn.title, dict) and isinstance(defn.fields[1].question, str)


@in_every_language
def test_every_example_of_the_documentation_is_written_correctly(language):
    blocks = YAML_BLOCK.findall(scenario_doc(language))
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
