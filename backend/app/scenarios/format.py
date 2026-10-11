"""How to write a scenario file: the description the admin shows next to «upload a file»
and gives out as a file.

The description is `README.md` of the scenarios folder, next to the ready scenarios it
talks about: wherever the app has that folder (an image, a mounted folder) it has the
description too. The complete example file inside it is given out separately, so that a
person can start from it.
"""

import re
from pathlib import Path

FORMAT_FILE = "README.md"
# the example file: the YAML block right after this mark
EXAMPLE_RE = re.compile(r"<!-- example-file -->\s*```yaml\n(.*?\n)```", re.DOTALL)


def read_format(path: Path) -> dict[str, str] | None:
    """{text, example} — the description (Markdown) and its example file (YAML); None
    when the folder has no description."""
    file = path / FORMAT_FILE
    if not file.is_file():
        return None
    text = file.read_text(encoding="utf-8")
    found = EXAMPLE_RE.search(text)
    return {"text": text, "example": found.group(1) if found else ""}
