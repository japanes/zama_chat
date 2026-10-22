"""Compact state for Jev (the `state` of /v1/evaluate).

Route sees the rewritten text, no history (docs/04 §5). When the rewrite came out shorter
than what the client wrote, their own words go along with it: a rewrite turns a message
into a few self-contained questions, and of a long message — a story, a document pasted
whole — it keeps a line or two. From stage 2 the open scenarios and their field states are
added as extra sections. The whole state is kept within Jev's window (32k tokens for the
state + the longest question): the client's text is not cut while it fits there.
"""

from collections.abc import Iterable

from app.decisions.limits import CONTEXT_TOKENS, estimate_tokens

# Jev's window less the room of a question, for a caller that does not know its questions.
DEFAULT_MAX_TOKENS = CONTEXT_TOKENS - 2000
TRIMMED = "…"
CLIENT = "Client's latest message"
OWN_WORDS = "The same message in the client's own words"


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """Cut the text so estimate_tokens() fits the budget; the start is kept."""
    if estimate_tokens(text) <= max_tokens:
        return text
    keep = max(0, (max_tokens - 1) * 3 - len(TRIMMED))
    return text[:keep].rstrip() + TRIMMED


def own_words_needed(rewritten: str, original: str) -> bool:
    """The rewrite has said less than the client: their words go to route too."""
    return len(" ".join(original.split())) > len(" ".join(rewritten.split()))


def _client_text(rewritten: str, original: str, max_tokens: int) -> str:
    text, own = rewritten.strip(), original.strip()
    if not own_words_needed(text, own):
        return f"{CLIENT}:\n" + trim_to_tokens(text, max_tokens - estimate_tokens(CLIENT))
    # Only a text longer than the whole window is cut, the rewritten one first: it is the
    # client's words retold.
    words = f"{OWN_WORDS}:\n" + trim_to_tokens(own, max_tokens - estimate_tokens(OWN_WORDS))
    room = max_tokens - estimate_tokens(words) - estimate_tokens(CLIENT)
    return f"{CLIENT}:\n" + trim_to_tokens(text, max(room, 0)) + "\n\n" + words


def render_route_state(
    rewritten: str,
    *,
    original: str = "",
    sections: Iterable[tuple[str, str]] = (),
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """ "Client's latest message" first (`original` — the client's own words, shown when the
    rewrite is shorter); extra sections (title, body) after it get what is left of the
    budget, in order."""
    head = _client_text(rewritten, original, max_tokens)
    parts = [head]
    left = max_tokens - estimate_tokens(head)
    for title, body in sections:
        if left <= 0:
            break
        part = trim_to_tokens(f"{title}:\n{body.strip()}", left)
        parts.append(part)
        left -= estimate_tokens(part)
    return "\n\n".join(parts)
