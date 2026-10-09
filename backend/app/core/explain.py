"""A turn explained for a person: what the bot understood from the message, what it
looked up, and what it decided to do. Built from what the turn already keeps (its route
and trace), so nothing new is stored and old turns are explained too.

The admin shows it as «why the bot answered this way»; the technical trace stays next
to it for those who need every call. Each line is a code with parameters — the admin
turns it into words in the reader's language. Scenarios and fields go by their keys:
the admin has their titles."""

import ast
from typing import Any

from app.search.step import BOTH, CATALOG, KNOWLEDGE

# how much of a list is named; the rest is a number
SHOWN = 4
VALUE_LIMIT = 200
# what happened to a run this turn → what the bot decided about it
_RUN_EVENTS = {
    "waiting": "wait_outside",  # all is collected: the request went to the outside system
    "receipt_review": "wait_review",  # a receipt the bot is not sure about: a person looks
    "completed": "completed",
    "abandoned": "abandoned",
}


def explain_turn(
    *,
    input_text: str | None,
    rewritten: str | None,
    route: dict[str, Any] | None,
    trace: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    route = route or {}
    steps: list[dict[str, Any]] = [s for s in (trace or {}).get("steps") or [] if _is(s, dict)]
    summary = _dict(_step(steps, "scenarios").get("summary"))
    plan = _dict(summary.get("plan"))
    answers = _dict(route.get("answers"))
    search = _dict(_step(steps, "search")) or _dict(route.get("search"))
    wants_human = route.get("wants_human") is True

    understood = _understood(input_text, rewritten, summary, answers, wants_human)
    found = _found(search)
    decided = _decided(summary, plan, steps, search, wants_human)
    return {"understood": understood, "found": found, "decided": decided}


def turn_failure(error: str | None) -> dict[str, Any] | None:
    """Why a failed turn failed, as a code with params the admin has words for; None for
    a turn without an error. The turn keeps its error as text for the log —
    "{'code': …, 'params': …}: message" — the code is read back from it; an error of the
    program itself (not an AppError) is `turn.failed` with its name."""
    if not error:
        return None
    found: dict[str, Any] = {}
    end = error.find("}: ")
    while end >= 0 and not found:
        try:
            found = _dict(ast.literal_eval(error[: end + 1]))
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            found = {}
        end = error.find("}: ", end + 1)
    code = found.get("code")
    if _is(code, str) and "." in code:
        return {"code": code, "params": _dict(found.get("params"))}
    name = code if _is(code, str) and code else _short(error)
    return {"code": "turn.failed", "params": {"error": name}}


def _understood(
    input_text: str | None,
    rewritten: str | None,
    summary: dict[str, Any],
    answers: dict[str, Any],
    wants_human: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if rewritten and _plain(rewritten) != _plain(input_text or ""):
        # the message as the bot reads it: with what was said before it
        out.append({"what": "meaning", "text": _short(rewritten)})
    for key in _list(summary.get("started")):
        out.append({"what": "started", "scenario": key})
    stop = summary.get("stop")
    if _is(stop, str) and ":" in stop:
        action, key = stop.split(":", 1)
        if action in ("cancelled", "paused"):
            out.append({"what": action, "scenario": key})

    issues = _dict(summary.get("issues"))
    named: set[str] = set()
    for ref, value in _dict(summary.get("extracted")).items():
        named.add(ref)
        if ref in issues:
            continue  # said below, with what is wrong
        out.append({"what": "answer", **_ref(ref), "value": _short(_text(value))})
    for ref in [*_list(summary.get("contacts")), *_list(summary.get("files"))]:
        if ref in named or ref in issues:
            continue
        named.add(ref)
        # a phone / an e-mail found by code, or a file: the value is in the run
        out.append({"what": "answer", **_ref(ref), "value": None})
    params = _dict(summary.get("issue_params"))
    for ref, issue in issues.items():
        out.append(
            {"what": "not_accepted", **_ref(ref), "issue": issue, "params": _dict(params.get(ref))}
        )

    confirm = summary.get("confirm")
    if confirm in ("yes", "no"):
        out.append({"what": f"confirm_{confirm}"})
    if answers.get("client_question") is True:
        out.append({"what": "question"})
    if wants_human:
        out.append({"what": "wants_human"})
    if not out:
        out.append({"what": "plain"})
    return out


def _found(search: dict[str, Any]) -> list[dict[str, Any]]:
    wanted = search.get("wanted")
    if wanted not in (CATALOG, KNOWLEDGE, BOTH):
        return []
    out: list[dict[str, Any]] = []
    items = [_item(i) for i in _list(search.get("items"))]
    pages = [_item(i) for i in _list(search.get("knowledge"))]
    if items:
        out.append({"what": "catalog", "items": items[:SHOWN], "more": max(0, len(items) - SHOWN)})
    if pages:
        out.append(
            {"what": "knowledge", "items": pages[:SHOWN], "more": max(0, len(pages) - SHOWN)}
        )
    if not out:
        out.append({"what": "nothing", "where": wanted})
    return out


def _decided(
    summary: dict[str, Any],
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    search: dict[str, Any],
    wants_human: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    scenario = plan.get("scenario")
    searched = search.get("wanted") in (CATALOG, KNOWLEDGE, BOTH)
    if plan.get("answer_client_question_first") or (searched and scenario):
        out.append({"what": "answer_first"})
    if plan.get("clarify"):
        out.append({"what": "clarify", "scenario": scenario, "field": plan["clarify"]})
    if plan.get("confirm") == "asked":
        out.append({"what": "ask_confirm", "scenario": scenario})
    elif plan.get("confirm") == "rejected":
        out.append({"what": "ask_what_wrong", "scenario": scenario})
    if plan.get("ask"):
        out.append({"what": "ask", "scenario": scenario, "field": plan["ask"]})
    passed_on = False
    for key, run in _dict(summary.get("runs")).items():
        # an event is kept as "name" or "name:field"
        events = {str(e).partition(":")[0] for e in _list(_dict(run).get("events"))}
        for event, what in _RUN_EVENTS.items():
            if event in events:
                out.append({"what": what, "scenario": key})
                passed_on = passed_on or what.startswith("wait_")
    if _list(plan.get("waiting")) and not passed_on:
        out.append({"what": "wait_outside", "scenario": scenario})
    if wants_human:
        out.append({"what": "handover"})
    variants = next((s.get("variants") for s in steps if _is(s.get("variants"), int)), None)
    if variants:
        # operator mode: no reply to the client, the variants wait for a person
        out.append({"what": "variants", "n": variants})
    if not out:
        if searched:
            out.append({"what": "say_unknown" if search.get("not_found") else "answer_question"})
        else:
            out.append({"what": "reply"})
    return out


# --- helpers ------------------------------------------------------------------------


def _is(value: Any, kind: type) -> bool:
    # bool is an int for Python, not for a person
    return isinstance(value, kind) and not (kind is int and isinstance(value, bool))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next((s for s in steps if s.get("step") == name), {})


def _ref(ref: str) -> dict[str, str]:
    scenario, _, field = str(ref).partition(".")
    return {"scenario": scenario, "field": field}


def _item(item: Any) -> dict[str, Any]:
    item = _dict(item)
    out: dict[str, Any] = {"title": _short(str(item.get("title") or item.get("ref") or ""))}
    if item.get("price") is not None:
        out["price"] = str(item["price"])
        out["currency"] = item.get("currency")
    return out


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_text(v)}" for k, v in value.items())
    return "" if value is None else str(value)


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= VALUE_LIMIT else text[: VALUE_LIMIT - 1] + "…"


def _plain(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!?")
