"""Ready scenarios to start a new one from: the files of the templates folder, as the
admin's «new scenario» page offers them. A template is an ordinary scenario file — the
ones `load-scenarios` loads; a broken file is skipped (and logged), the others stay.

A template is only a draft for the editor: it is checked and saved the usual way, under
the tenant's own key, so nothing here writes to the database.
"""

from pathlib import Path
from typing import Any

from app.errors import AppError
from app.logging import get_logger
from app.scenarios.loader import read_file, scenario_files
from app.scenarios.schema import parse_definition
from app.scenarios.store import definition_json

log = get_logger(__name__)


def list_templates(path: Path) -> list[dict[str, Any]]:
    """[{key, title, description, language, fields: [{key, type, title}], definition}] —
    texts as written (a string or {language: text}), in the order of the file names."""
    if not path.is_dir():
        return []
    out = []
    for file in scenario_files(path):
        try:
            defn = parse_definition(read_file(file))
        except AppError as exc:
            log.warning("scenario_template_broken", path=str(file), code=exc.code)
            continue
        out.append(
            {
                "key": defn.key,
                "title": defn.title,
                "description": defn.description,
                "language": defn.language,
                # what the bot asks, to show a template at a glance
                "fields": [{"key": f.key, "type": f.type, "title": f.title} for f in defn.fields],
                "definition": definition_json(defn),
            }
        )
    return out
