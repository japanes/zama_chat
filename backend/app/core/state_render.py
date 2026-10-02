"""Compact state for Jev (the `state` of /v1/evaluate).

Route sees ONLY the rewritten text (docs/04 §5): no original message, no history. From
stage 2 the open scenarios and their field states are added as extra sections. The whole
state is kept within a token budget: Jev's context is 32k for state + the longest question.
"""

from collections.abc import Iterable

from app.decisions.limits import estimate_tokens

DEFAULT_MAX_TOKENS = 4000
TRIMMED = "…"


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """Cut the text so estimate_tokens() fits the budget; the start is kept."""
    if estimate_tokens(text) <= max_tokens:
        return text
    keep = max(0, (max_tokens - 1) * 3 - len(TRIMMED))
    return text[:keep].rstrip() + TRIMMED


def render_route_state(
    rewritten: str,
    *,
    sections: Iterable[tuple[str, str]] = (),
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """ "Client's latest message" first; extra sections (title, body) after it get what is
    left of the budget, in order. The client's text gets at most half of the budget."""
    head = "Client's latest message:\n" + trim_to_tokens(rewritten.strip(), max_tokens // 2)
    parts = [head]
    left = max_tokens - estimate_tokens(head)
    for title, body in sections:
        if left <= 0:
            break
        part = trim_to_tokens(f"{title}:\n{body.strip()}", left)
        parts.append(part)
        left -= estimate_tokens(part)
    return "\n\n".join(parts)
