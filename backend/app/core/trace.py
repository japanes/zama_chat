"""Turn trace (turns.trace): steps with timing and links to llm_calls / decisions, so
everything about one turn can be found by turn_id."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class Trace:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {}

    @contextmanager
    def step(self, name: str, **info: Any) -> Iterator[dict[str, Any]]:
        entry: dict[str, Any] = {"step": name, **info}
        started = time.monotonic()
        try:
            yield entry
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            entry["ms"] = int((time.monotonic() - started) * 1000)
            self.steps.append(entry)

    def note(self, key: str, value: Any) -> None:
        self.data[key] = value

    def to_dict(self) -> dict[str, Any]:
        return {"steps": self.steps, **self.data}
