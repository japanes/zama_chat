"""Compact state for Jev (the `state` of /v1/evaluate).

Route sees the rewritten text, no history (docs/04 §5). When the rewrite came out shorter
than what the client wrote, the client's own words are the message and the rewrite follows
them as a retelling: a rewrite turns a message into a few self-contained questions, and of
a long message — a story, a document pasted whole — it keeps a line or two, while every
question of route asks about «the client's latest message». From stage 2 the open
scenarios and their field states are added as extra sections.

A request to Jev must fit its window (decisions/limits.py). A message that does not fit
next to the questions is not cut: it is split into parts, each sent with all the questions
and the same sections, and the answers of the parts are put together
(decisions/engine.merge_parts).
"""

import bisect
import re
from collections.abc import Iterable

from app.decisions.limits import REQUEST_TOKENS, WRAPPER_TOKENS, estimate_tokens

# Jev's window less the room of a few questions, for a caller that does not know its
# questions.
DEFAULT_MAX_TOKENS = REQUEST_TOKENS - WRAPPER_TOKENS - 2000
TRIMMED = "…"
CLIENT = "Client's latest message"
RETOLD = "The same message retold with the context of the dialogue"
# a piece is never shorter than this: a split into crumbs would say nothing either
MIN_PIECE_TOKENS = 200
# the widest break that works: paragraphs, lines, sentences, words
_BREAKS = (
    (re.compile(r"\n\s*\n"), "\n\n"),
    (re.compile(r"\n"), "\n"),
    (re.compile(r"(?<=[.!?…])\s+"), " "),
    (re.compile(r"\s+"), " "),
)
_JOIN = "\n\n"


def fit_prefix(text: str, max_tokens: int) -> int:
    """The length of the longest start of the text that fits the budget."""
    return (
        bisect.bisect_right(
            range(len(text) + 1), max_tokens, key=lambda n: estimate_tokens(text[:n])
        )
        - 1
    )


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """Cut the text so estimate_tokens() fits the budget; the start is kept."""
    if estimate_tokens(text) <= max_tokens:
        return text
    keep = fit_prefix(text, max_tokens - estimate_tokens(TRIMMED))
    return text[: max(0, keep)].rstrip() + TRIMMED


def split_to_tokens(text: str, max_tokens: int) -> list[str]:
    """The text in pieces of at most `max_tokens` each, cut on the widest break that works:
    paragraphs, then lines, sentences, words; a word longer than a piece is cut where the
    piece ends. Nothing is dropped: the pieces joined are the text, give or take the
    whitespace at the cuts."""
    text = text.strip()
    if not text:
        return []
    max_tokens = max(max_tokens, MIN_PIECE_TOKENS)
    if estimate_tokens(text) <= max_tokens:
        return [text]
    return _split(text, max_tokens, 0)


def _split(text: str, max_tokens: int, level: int) -> list[str]:
    if level == len(_BREAKS):
        return _cut(text, max_tokens)
    pattern, joiner = _BREAKS[level]
    units = [u.strip() for u in pattern.split(text) if u.strip()]
    if len(units) < 2:
        return _split(text, max_tokens, level + 1)
    glue = estimate_tokens(joiner)
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for unit in units:
        tokens = estimate_tokens(unit)
        if tokens > max_tokens:
            if current:
                pieces.append(joiner.join(current))
                current, current_tokens = [], 0
            pieces.extend(_split(unit, max_tokens, level + 1))
            continue
        if current and current_tokens + glue + tokens > max_tokens:
            pieces.append(joiner.join(current))
            current, current_tokens = [], 0
        current_tokens += tokens + (glue if current else 0)
        current.append(unit)
    if current:
        pieces.append(joiner.join(current))
    return pieces


def _cut(text: str, max_tokens: int) -> list[str]:
    pieces = []
    while text:
        keep = max(1, fit_prefix(text, max_tokens))
        pieces.append(text[:keep])
        text = text[keep:]
    return pieces


def part_label(label: str, number: int, total: int) -> str:
    return label if total == 1 else f"{label}, part {number} of {total}"


def split_state(label: str, text: str, max_tokens: int, *, after: str = "") -> list[str]:
    """`label:\\ntext` (+ `after`) as one state, or as parts of the text when it does not
    fit `max_tokens`: «label, part i of n», each part followed by `after`."""
    text = text.strip()
    tail = f"{_JOIN}{after}" if after else ""
    whole = f"{label}:\n{text}{tail}"
    if estimate_tokens(whole) <= max_tokens:
        return [whole]
    room = max_tokens - estimate_tokens(f"{part_label(label, 99, 99)}:\n") - estimate_tokens(tail)
    pieces = split_to_tokens(text, room)
    return [
        f"{part_label(label, n, len(pieces))}:\n{piece}{tail}"
        for n, piece in enumerate(pieces, start=1)
    ]


def own_words_needed(rewritten: str, original: str) -> bool:
    """The rewrite has said less than the client: their own words are the message."""
    return len(" ".join(original.split())) > len(" ".join(rewritten.split()))


def _client_texts(rewritten: str, original: str) -> list[tuple[str, str]]:
    text, own = rewritten.strip(), original.strip()
    if not own_words_needed(text, own):
        return [(CLIENT, text)]
    return [(CLIENT, own), (RETOLD, text)]


def _sections_text(sections: Iterable[tuple[str, str]], max_tokens: int | None) -> str:
    """The sections in order; with a budget each gets what the ones before it left."""
    parts: list[str] = []
    left = max_tokens
    for title, body in sections:
        part = f"{title}:\n{body.strip()}"
        if left is not None:
            if left <= 0:
                break
            part = trim_to_tokens(part, left)
            left -= estimate_tokens(part)
        parts.append(part)
    return _JOIN.join(parts)


def render_route_parts(
    rewritten: str,
    *,
    original: str = "",
    sections: Iterable[tuple[str, str]] = (),
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[str]:
    """The route state, each part within `max_tokens`.

    "Client's latest message" first (`original` — the client's own words: the message
    itself when the rewrite is shorter, the rewrite follows them); extra sections (title,
    body) after it. One part while everything fits. A longer message is split: the
    sections take what the message leaves, but at least half of a part (they are previews
    of the scenario state; what does not fit there is cut from the end), and every part
    carries a piece of the message and the same sections. The retelling goes with every
    piece of the client's words while it is short (a quarter of the room at most); a
    longer one comes in parts of its own."""
    sections = list(sections)
    texts = _client_texts(rewritten, original)
    head = _JOIN.join(f"{label}:\n{body}" for label, body in texts)
    every = _sections_text(sections, None)
    whole = _JOIN.join(p for p in (head, every) if p)
    if estimate_tokens(whole) <= max_tokens:
        return [whole]
    after = _sections_text(
        sections, max(max_tokens // 2, max_tokens - estimate_tokens(head) - estimate_tokens(_JOIN))
    )
    room = max_tokens - (estimate_tokens(after) + estimate_tokens(_JOIN) if after else 0)
    if estimate_tokens(head) <= room:
        return [_JOIN.join(p for p in (head, after) if p)]
    (label, words), *rest = texts
    retold = rest[0][1] if rest else ""
    with_each = ""
    if retold and estimate_tokens(f"{RETOLD}:\n{retold}") <= room // 4:
        with_each, retold = f"{RETOLD}:\n{retold}", ""
    tail = _JOIN.join(p for p in (with_each, after) if p)
    parts = split_state(label, words, max_tokens, after=tail)
    if retold:
        parts += split_state(RETOLD, retold, max_tokens, after=after)
    return parts
