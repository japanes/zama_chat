"""Per-question policy: the confidence threshold below which the answer is re-asked.

Stage 1: one default threshold + overrides by key or fnmatch pattern ("start:*").
Stage 7 adds the mode (jev | llm | shadow) and what to do below the threshold.
"""

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

# TODO(review): starting value; the real ones come from the Jev probe report
# (tmp/jev-probe/report.md) and later from the decisions log (stage 7).
DEFAULT_THRESHOLD = 0.7


@dataclass
class DecisionPolicy:
    default_threshold: float = DEFAULT_THRESHOLD
    # exact key or fnmatch pattern → threshold; exact keys win, then the longest pattern
    thresholds: dict[str, float] = field(default_factory=dict)

    def threshold(self, key: str) -> float:
        if key in self.thresholds:
            return self.thresholds[key]
        matches = [p for p in self.thresholds if fnmatchcase(key, p)]
        if matches:
            return self.thresholds[max(matches, key=len)]
        return self.default_threshold

    def is_sure(self, key: str, confidence: float) -> bool:
        return confidence >= self.threshold(key)
