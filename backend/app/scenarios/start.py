"""Who starts and who gets the focus (docs/04 §5 «Запуск сценария»). Pure functions.

A scenario fires when the sum of weights of its "yes" start answers reaches its
threshold. Then:
- a scenario in focus keeps it; the fired ones start paused;
- a `stop` in the same message that paused / cancelled the focus frees it first;
- no focus → the biggest sum gets it; on a tie — the one higher in the channel's list;
- a fired scenario that is already open (paused) is not created again; it takes the
  focus if the focus is free.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Vote:
    question: str  # route question key
    weight: int
    yes: bool


@dataclass(frozen=True)
class Candidate:
    key: str  # scenario key
    position: int  # order in the channel's list
    threshold: int
    votes: tuple[Vote, ...]
    open: bool = False  # already open (paused / waiting) in this conversation

    @property
    def total(self) -> int:
        return sum(v.weight for v in self.votes if v.yes)

    @property
    def fired(self) -> bool:
        return self.total >= self.threshold


@dataclass
class StartResult:
    fired: list[str] = field(default_factory=list)  # in channel order
    new: list[str] = field(default_factory=list)  # runs to create
    focus: str | None = None  # scenario in focus after this message
    focus_changed: bool = False
    sums: dict[str, dict[str, object]] = field(default_factory=dict)  # for the trace
    # fired, but an open (or a stronger new) scenario is incompatible with it
    blocked: list[str] = field(default_factory=list)


def decide_start(candidates: Iterable[Candidate], focus: str | None) -> StartResult:
    """`focus` — the scenario in focus after `stop` was applied (None if free).
    The scenario in focus is never a candidate."""
    ordered: Sequence[Candidate] = sorted(
        (c for c in candidates if c.key != focus), key=lambda c: c.position
    )
    result = StartResult(focus=focus)
    for c in ordered:
        result.sums[c.key] = {
            "sum": c.total,
            "threshold": c.threshold,
            "yes": [v.question for v in c.votes if v.yes],
            "fired": c.fired,
        }
    fired = [c for c in ordered if c.fired]
    result.fired = [c.key for c in fired]
    result.new = [c.key for c in fired if not c.open]
    if focus is None and fired:
        # biggest sum; on a tie the first in the channel order (max keeps the first)
        best = max(fired, key=lambda c: c.total)
        result.focus = best.key
        result.focus_changed = True
    return result
