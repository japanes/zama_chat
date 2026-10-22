"""Catalog and knowledge search for a turn (docs/04 stage 5).

1. Candidates: the catalog by words (Elasticsearch) and by meaning (Qdrant), the two
   lists merged by RRF (reciprocal rank fusion: an item high in both lists comes first);
   the knowledge base by meaning (Qdrant), first in the reply language, then in any.
2. Refine: one decider batch (Jev) — for every candidate a yes/no question "does it fit
   the client's request?" (`cand:<n>` / `kb:<n>`). Only the ones that fit go further.
3. The best few go into the reply prompt's `context`. Nothing fits → empty context and
   the legacy "I don't know" rules answer (or a handover — tenant setting
   `unknown_answer`).

An index that does not answer is skipped with a trace note; the turn never fails
because of search.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, exists, select

from app.core.state_render import split_state
from app.db.models import (
    CatalogItem,
    DecisionType,
    IndexStatus,
    KnowledgeChunk,
    KnowledgeSource,
)
from app.decisions.engine import Answer, DecideContext, Question, run_decision_parts
from app.decisions.limits import part_room
from app.errors import AppError
from app.llm.openai_client import LLMRequestError, LLMTransportError
from app.logging import get_logger
from app.search.base import CatalogFilters, IndexHit, SearchBadRequest, SearchUnavailable
from app.search.index import CATALOG, KNOWLEDGE, clients, embed_model
from app.search.text import CHUNK_CHARS, item_text

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

# TODO(review): sizes and the threshold; tune with make eval
CANDIDATES = 100  # catalog candidates for the decider (Jev batch)
KNOWLEDGE_CANDIDATES = 20
CONTEXT_ITEMS = 5
CONTEXT_KNOWLEDGE = 4
MATCH_THRESHOLD = 0.5  # p("fits") from the decider
RRF_K = 60
QUESTION_TEXT_CHARS = 600  # details of a catalog item shown to the decider
# a knowledge chunk is shown whole (title + text): an answer in a cut-off half would be
# thrown away as "does not fit"
KNOWLEDGE_TEXT_CHARS = CHUNK_CHARS + 400


@dataclass
class Found:
    kind: str  # catalog | knowledge
    id: str  # catalog item id / knowledge chunk id
    ref: str  # external_id / knowledge source id
    title: str
    text: str
    language: str
    url: str | None = None
    category: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    image_url: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    rank: int = 0  # position after the merge, 0 = best
    match: float | None = None  # p("fits") from the decider

    def to_context(self, n: int | None = None) -> dict[str, Any]:
        """An entry of the reply prompt's `context`."""
        if self.kind == KNOWLEDGE:
            out: dict[str, Any] = {"kind": "knowledge", "title": self.title, "text": self.text}
            if self.url:
                out["url"] = self.url
            return out
        out = {"kind": "item", "n": n, "title": self.title}
        if self.category:
            out["category"] = self.category
        if self.text:
            out["description"] = self.text
        if self.price is not None:
            out["price"] = f"{self.price:.2f}"
            if self.currency:
                out["currency"] = self.currency
        if self.url:
            out["url"] = self.url
        return out

    def to_presented(self, n: int) -> dict[str, Any]:
        """An entry of state.last_presented (search/presented.py)."""
        out: dict[str, Any] = {"n": n, "ref": self.ref, "title": self.title}
        if self.category:
            out["category"] = self.category
        if self.price is not None:
            out["price"] = f"{self.price:.2f}"
            out["currency"] = self.currency
        return out

    def to_dict(self) -> dict[str, Any]:
        """For the admin's test search and the trace."""
        out = self.to_context()
        out.pop("n", None)
        out.update({"id": self.id, "ref": self.ref, "rank": self.rank, "match": self.match})
        return out


@dataclass
class SearchNotes:
    """What happened, for the trace: which index answered, errors."""

    notes: list[dict[str, Any]] = field(default_factory=list)

    def add(self, what: str, **info: Any) -> None:
        self.notes.append({"what": what, **info})


def rrf(lists: Sequence[Sequence[IndexHit]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion: score = Σ 1 / (k + rank) over the lists an id is in."""
    scores: dict[str, float] = {}
    for hits in lists:
        for rank, hit in enumerate(hits):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
    # stable: equal scores keep the order of the first list
    return sorted(scores, key=lambda i: -scores[i])


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Content:
    """What the tenant has to search in (route asks `search` only then)."""

    catalog: bool = False
    knowledge: bool = False

    @property
    def any(self) -> bool:
        return self.catalog or self.knowledge


async def tenant_content(services: "Services", tenant_id: uuid.UUID) -> Content:
    async with services.sm() as session:
        catalog = await session.scalar(
            select(
                exists().where(
                    and_(
                        CatalogItem.tenant_id == tenant_id,
                        CatalogItem.is_active.is_(True),
                        CatalogItem.indexed_at.is_not(None),
                    )
                )
            )
        )
        knowledge = await session.scalar(
            select(
                exists().where(
                    and_(
                        KnowledgeSource.tenant_id == tenant_id,
                        KnowledgeSource.is_active.is_(True),
                        KnowledgeSource.chunks_count > 0,
                    )
                )
            )
        )
    return Content(catalog=bool(catalog), knowledge=bool(knowledge))


async def _query_vector(
    services: "Services", tenant_id: uuid.UUID, query: str, notes: SearchNotes
) -> tuple[str, list[float]] | None:
    model = await embed_model(services, tenant_id)
    if model is None:
        notes.add("vectors_skipped", reason="embed_unassigned")
        return None
    try:
        vectors, _ = await services.llm.embed([query], tenant_id=tenant_id)
    except (LLMTransportError, LLMRequestError, AppError) as exc:
        notes.add("embed_error", error=str(exc)[:200])
        return None
    return model, vectors[0]


async def find_catalog(
    services: "Services",
    tenant_id: uuid.UUID,
    query: str,
    *,
    filters: CatalogFilters | None = None,
    limit: int = CANDIDATES,
    notes: SearchNotes | None = None,
) -> list[Found]:
    filters = filters or CatalogFilters()
    notes = notes if notes is not None else SearchNotes()
    search = clients(services)
    lists: list[list[IndexHit]] = []
    if search.words is not None:
        try:
            hits = await search.words.search(tenant_id, query, filters=filters, limit=limit)
            lists.append(hits)
            notes.add("words", hits=len(hits))
        except (SearchUnavailable, SearchBadRequest) as exc:
            notes.add("words_error", error=str(exc)[:200])
            log.warning("search.words_error", error=str(exc)[:200])
    if search.vectors is not None and query.strip():
        qv = await _query_vector(services, tenant_id, query, notes)
        if qv is not None:
            model, vector = qv
            match: dict[str, Any] = {"tenant_id": str(tenant_id)}
            if filters.categories:
                match["category"] = filters.categories
            try:
                hits = await search.vectors.search(
                    search.collection(CATALOG, model), vector, match=match, limit=limit
                )
                lists.append(hits)
                notes.add("vectors", hits=len(hits))
            except (SearchUnavailable, SearchBadRequest) as exc:
                notes.add("vectors_error", error=str(exc)[:200])
                log.warning("search.vectors_error", error=str(exc)[:200])
    ids = [i for i in (_uuid(x) for x in rrf(lists)[:limit]) if i is not None]
    if not ids:
        return []
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(CatalogItem).where(
                    CatalogItem.id.in_(ids),
                    CatalogItem.tenant_id == tenant_id,
                    CatalogItem.is_active.is_(True),
                )
            )
        ).scalars()
        by_id = {row.id: row for row in rows}
    found = []
    for item_id in ids:
        row = by_id.get(item_id)
        # vectors have no price filter: check it here
        if row is None or not _fits_filters(row, filters):
            continue
        found.append(_from_item(row, rank=len(found)))
    return found


def _fits_filters(item: CatalogItem, filters: CatalogFilters) -> bool:
    if filters.categories and item.category not in filters.categories:
        return False
    if filters.price_min is not None and (item.price is None or item.price < filters.price_min):
        return False
    if filters.price_max is not None and (item.price is None or item.price > filters.price_max):
        return False
    for key, values in filters.attributes.items():
        have = (item.attributes or {}).get(key)
        have_list = have if isinstance(have, list) else [have]
        if not {str(v) for v in have_list} & {str(v) for v in values}:
            return False
    return True


def _from_item(item: CatalogItem, *, rank: int) -> Found:
    return Found(
        kind=CATALOG,
        id=str(item.id),
        ref=item.external_id,
        title=item.title,
        text=(item.description or "").strip(),
        language=item.language,
        url=item.url,
        category=item.category,
        price=item.price,
        currency=item.currency,
        image_url=item.image_url,
        attributes=item.attributes or {},
        rank=rank,
    )


async def find_knowledge(
    services: "Services",
    tenant_id: uuid.UUID,
    query: str,
    *,
    language: str | None = None,
    limit: int = KNOWLEDGE_CANDIDATES,
    notes: SearchNotes | None = None,
) -> list[Found]:
    notes = notes if notes is not None else SearchNotes()
    search = clients(services)
    if search.vectors is None or not query.strip():
        return []
    qv = await _query_vector(services, tenant_id, query, notes)
    if qv is None:
        return []
    model, vector = qv
    collection = search.collection(KNOWLEDGE, model)
    hits: list[IndexHit] = []
    try:
        # the reply language first, then any language of the tenant
        if language:
            hits = await search.vectors.search(
                collection,
                vector,
                match={"tenant_id": str(tenant_id), "language": language},
                limit=limit,
            )
        if not hits:
            hits = await search.vectors.search(
                collection, vector, match={"tenant_id": str(tenant_id)}, limit=limit
            )
    except (SearchUnavailable, SearchBadRequest) as exc:
        notes.add("knowledge_error", error=str(exc)[:200])
        log.warning("search.knowledge_error", error=str(exc)[:200])
        return []
    notes.add("knowledge", hits=len(hits))
    ids = [i for i in (_uuid(h.id) for h in hits) if i is not None]
    if not ids:
        return []
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(KnowledgeChunk, KnowledgeSource)
                .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
                .where(
                    KnowledgeChunk.id.in_(ids),
                    KnowledgeChunk.tenant_id == tenant_id,
                    KnowledgeSource.is_active.is_(True),
                    KnowledgeSource.index_status != IndexStatus.ERROR,
                )
            )
        ).all()
    by_id = {chunk.id: (chunk, source) for chunk, source in rows}
    found = []
    for chunk_id in ids:
        pair = by_id.get(chunk_id)
        if pair is None:
            continue
        chunk, source = pair
        found.append(
            Found(
                kind=KNOWLEDGE,
                id=str(chunk.id),
                ref=str(source.id),
                title=source.title,
                text=chunk.text,
                language=chunk.language,
                url=source.url,
                rank=len(found),
            )
        )
    return found


# --- refine: one decider batch over the candidates ------------------------------------


def _short(text: str, limit: int = QUESTION_TEXT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def candidate_question(key: str, found: Found) -> Question:
    # TODO(review): wording; English for now (docs/04 §5: the probe decides the language)
    if found.kind == KNOWLEDGE:
        # an indexed chunk already starts with the title of its source
        text = found.text if found.text.startswith(found.title) else f"{found.title}. {found.text}"
        return Question(
            key,
            DecisionType.BOOLEAN,
            "Does this text from the company's knowledge base answer the client's question "
            f"(fully or in part)?\nText: {_short(text, KNOWLEDGE_TEXT_CHARS)}",
            {
                "true": "The text has the information the client asks for.",
                "false": "The text is about something else or does not help to answer.",
            },
        )
    lines = [f"Item: {found.title}"]
    if found.category:
        lines.append(f"Category: {found.category}")
    if found.price is not None:
        lines.append(f"Price: {found.price:.2f} {found.currency or ''}".rstrip())
    details = _short(item_text_of(found))
    if details:
        lines.append(f"Details: {details}")
    return Question(
        key,
        DecisionType.BOOLEAN,
        "Does this item of the catalog match what the client is looking for or asking "
        "about?\n" + "\n".join(lines),
        {
            "true": "The item matches the client's request (the kind of product or service, "
            "and the conditions the client named).",
            "false": "The item does not match the request or contradicts a condition the "
            "client named.",
        },
    )


def item_text_of(found: Found) -> str:
    parts = [found.text] if found.text else []
    for key, value in sorted(found.attributes.items()):
        if value not in (None, "", []):
            parts.append(f"{key}: {value}")
    return "; ".join(parts)


def p_fits(answer: Answer | None) -> float:
    if answer is None or answer.value is None:
        return 0.0
    if "true" in answer.probabilities:
        return float(answer.probabilities["true"])
    return answer.confidence if answer.value else 1.0 - answer.confidence


async def refine(
    services: "Services",
    ctx: DecideContext,
    request: str,
    groups: dict[str, tuple[list[Found], int]],
    *,
    threshold: float = MATCH_THRESHOLD,
    trace_step: dict[str, Any] | None = None,
) -> dict[str, list[Found]]:
    """Ask the decider about every candidate in ONE batch.

    `groups`: question prefix → (candidates, how many to keep), e.g.
    {"cand": (catalog, 5), "kb": (knowledge, 4)}. Returns prefix → the candidates that
    fit, best first."""
    keys: dict[str, tuple[str, Found]] = {}
    for prefix, (found, _) in groups.items():
        for n, item in enumerate(found, start=1):
            keys[f"{prefix}:{n}"] = (prefix, item)
    out: dict[str, list[Found]] = {prefix: [] for prefix in groups}
    if not keys:
        return out
    questions = [candidate_question(key, item) for key, (_, item) in keys.items()]
    # a request longer than Jev's window goes in parts: a candidate fits when a part says so
    parts = split_state("Client's request", request, part_room(questions))
    decisions = await run_decision_parts(
        services, [(part, questions) for part in parts], ctx, trace_step=trace_step
    )
    for key, (prefix, item) in keys.items():
        item.match = round(p_fits(decisions.answers.get(key)), 4)
        if item.match >= threshold:
            out[prefix].append(item)
    for prefix, (_, keep) in groups.items():
        out[prefix].sort(key=lambda f: (-(f.match or 0.0), f.rank))
        out[prefix] = out[prefix][:keep]
    return out


def item_for(item: CatalogItem) -> Found:
    """A catalog row as a search result (the catalog field of a scenario)."""
    found = _from_item(item, rank=0)
    found.text = (item.description or "").strip() or item_text(item)
    return found
