"""The admin's scenario editor keeps a scenario as it is: a file opened in the editor and
saved without a change is the same definition in the server's eyes — the same version
(store.save_scenario compares definition_json). Runs the editor's own code
(admin/utils/scenario.ts) with node; skipped where node or the admin's packages are not
installed (the backend image)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.scenarios.schema import parse_definition
from app.scenarios.store import definition_json

REPO = Path(__file__).resolve().parents[3]
ADMIN = REPO / "admin"
SCRIPT = ADMIN / "tests" / "roundtrip.mjs"
FILES = [*sorted((REPO / "scenarios").glob("*.yaml")), ADMIN / "tests" / "full-scenario.json"]


def _node() -> str | None:
    """node that runs TypeScript as it is (type stripping: 22.18+ / 23.6+)."""
    node = shutil.which("node")
    if node is None or not (ADMIN / "node_modules" / "js-yaml").is_dir():
        return None
    found = re.match(r"v(\d+)\.(\d+)", subprocess.check_output([node, "--version"], text=True))
    if found is None:
        return None
    major, minor = int(found[1]), int(found[2])
    return (
        node
        if major >= 24 or (major == 23 and minor >= 6) or (major == 22 and minor >= 18)
        else None
    )


@pytest.fixture(scope="module")
def saved() -> list[dict]:
    node = _node()
    if node is None:
        pytest.skip("node 22.18+ with the admin's packages is needed")
    out = subprocess.run(
        [node, str(SCRIPT), *map(str, FILES)],
        cwd=ADMIN,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("name", [f.name for f in FILES])
def test_saved_without_a_change_is_the_same_version(saved: list[dict], name: str) -> None:
    item = next(x for x in saved if Path(x["file"]).name == name)
    source = definition_json(parse_definition(item["source"]))
    assert definition_json(parse_definition(item["saved"])) == source


def test_the_full_file_uses_every_new_setting(saved: list[dict]) -> None:
    item = next(x for x in saved if x["file"].endswith("full-scenario.json"))
    d = definition_json(parse_definition(item["saved"]))
    assert d["completion"] == "by_caller"
    assert d["added"] == {"wording": "as_sent", "close_after": 5, "max_fields": 20}
    assert d["waiting"]["say"] and d["ask_rules"] and d["history_messages"] == 12
    assert d["only_scenario"] is True
    by_key = {f["key"]: f for f in d["fields"]}
    assert by_key["stakes"]["show_if"]["on_unknown"] == "skip"
    assert by_key["stakes"]["show_if"]["recheck"] == "once"
    assert by_key["angle"]["options_from"] == "caller"
