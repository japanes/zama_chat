"""`conversations.state["last_presented"]`: the catalog options the bot showed last.

The rewrite step gets them so «давайте другий» becomes the full title of option 2, and
a catalog field of a scenario picks its value among them. The list is replaced every
time the bot shows new options; it is not cleared by other turns (the client may come
back to it a few messages later).
"""

from typing import Any

STATE_KEY = "last_presented"
MAX_PRESENTED = 10


def presented(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = (state or {}).get(STATE_KEY)
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("ref") and i.get("title")]


def with_presented(
    state: dict[str, Any] | None, options: list[dict[str, Any]], *, at: str
) -> dict[str, Any]:
    """A copy of the state with new options: [{n, ref, title, price?, currency?}]."""
    out = dict(state or {})
    out[STATE_KEY] = options[:MAX_PRESENTED]
    out[f"{STATE_KEY}_at"] = at
    return out


def rewrite_view(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the rewrite model sees: number, title, price."""
    out = []
    for item in options:
        view: dict[str, Any] = {"n": item.get("n"), "title": item["title"]}
        if item.get("price") is not None:
            view["price"] = f"{item['price']} {item.get('currency') or ''}".strip()
        if item.get("category"):
            view["category"] = item["category"]
        out.append(view)
    return out
