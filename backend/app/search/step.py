"""The search part of a turn: the route question `search` and the search itself.

- `search_question()` — added to the route batch only when the tenant has something
  indexed; its options are what the tenant has (catalog / knowledge / both);
- `search_step()` — after the route: finds, refines and returns the `context` for the
  reply prompt and the new `last_presented` (the catalog items the reply will show).

The search works on the REWRITTEN text: it is self-contained and already in a language
of the tenant (docs/04 §5 «Языки»).
"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext
from app.db.models import DecisionType
from app.decisions.engine import DecisionSet, Question
from app.search.index import CATALOG, KNOWLEDGE
from app.search.service import (
    CONTEXT_ITEMS,
    CONTEXT_KNOWLEDGE,
    Content,
    Found,
    SearchNotes,
    find_catalog,
    find_knowledge,
    refine,
)

if TYPE_CHECKING:
    from app.services import Services

SEARCH_KEY = "search"
NONE = "none"
BOTH = "both"
UNKNOWN_SAY = "say"
UNKNOWN_HANDOVER = "handover"

# TODO(review): wording (same note as the other route questions)
_CRITERIA = {
    NONE: "The message needs no company information: a greeting, small talk, an answer "
    "to the bot's question, a request for a human, or a question about the client's own "
    "data.",
    KNOWLEDGE: "The client asks about the company's terms, policies, delivery, payment, "
    "address, opening hours or other general information.",
    CATALOG: "The client looks for a product or a service, asks what is available, its "
    "price, characteristics, or wants a recommendation or a comparison.",
    BOTH: "The client asks both about products / services and about the company's terms "
    "(for example the price of an item and the delivery).",
}


def search_question(content: Content) -> Question | None:
    if not content.any:
        return None
    options = [NONE]
    if content.knowledge:
        options.append(KNOWLEDGE)
    if content.catalog:
        options.append(CATALOG)
    if content.catalog and content.knowledge:
        options.append(BOTH)
    return Question(
        SEARCH_KEY,
        DecisionType.CHOICE,
        "Does the bot need to look up company information to answer the client's latest "
        "message? Where?",
        {o: _CRITERIA[o] for o in options},
        neutral=NONE,
        # unsure: looked up all the same — a needless lookup costs less than a reply
        # without the company's facts
        unsure_as={o: o for o in options if o != NONE},
    )


@dataclass
class SearchOutcome:
    wanted: str = NONE  # none / knowledge / catalog / both
    context: list[dict[str, Any]] = field(default_factory=list)
    # catalog items the reply shows → state.last_presented; None = keep the old list
    presented: list[dict[str, Any]] | None = None
    items: list[Found] = field(default_factory=list)
    knowledge: list[Found] = field(default_factory=list)

    @property
    def searched(self) -> bool:
        return self.wanted != NONE

    @property
    def not_found(self) -> bool:
        """The client needed information and nothing fit."""
        return self.searched and not self.context

    def to_trace(self) -> dict[str, Any]:
        return {
            "wanted": self.wanted,
            "items": [f.to_dict() for f in self.items],
            "knowledge": [f.to_dict() for f in self.knowledge],
            "not_found": self.not_found,
        }


async def search_step(
    services: "Services",
    ctx: TurnContext,
    rewritten: str,
    decisions: DecisionSet,
    reply_language: str,
    content: Content,
) -> SearchOutcome:
    wanted = decisions.value(SEARCH_KEY, NONE) if content.any else NONE
    outcome = SearchOutcome(wanted=wanted)
    if wanted == NONE:
        return outcome
    want_catalog = wanted in (CATALOG, BOTH) and content.catalog
    want_knowledge = wanted in (KNOWLEDGE, BOTH) and content.knowledge
    notes = SearchNotes()
    with ctx.trace.step("search", wanted=wanted) as step:
        catalog_task = (
            find_catalog(services, ctx.tenant_id, rewritten, notes=notes)
            if want_catalog
            else _nothing()
        )
        knowledge_task = (
            find_knowledge(services, ctx.tenant_id, rewritten, language=reply_language, notes=notes)
            if want_knowledge
            else _nothing()
        )
        catalog, knowledge = await asyncio.gather(catalog_task, knowledge_task)
        step["candidates"] = {"catalog": len(catalog), "knowledge": len(knowledge)}
        kept = await refine(
            services,
            ctx.decide_context(),
            rewritten,
            {"cand": (catalog, CONTEXT_ITEMS), "kb": (knowledge, CONTEXT_KNOWLEDGE)},
            trace_step=step,
        )
        outcome.items = kept["cand"]
        outcome.knowledge = kept["kb"]
        outcome.context = [
            *(f.to_context(n) for n, f in enumerate(outcome.items, start=1)),
            *(f.to_context() for f in outcome.knowledge),
        ]
        if outcome.items:
            outcome.presented = [f.to_presented(n) for n, f in enumerate(outcome.items, start=1)]
        step.update(outcome.to_trace())
        step["notes"] = notes.notes
    return outcome


async def _nothing() -> list[Found]:
    return []
