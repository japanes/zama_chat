"""Scenario fields of type `catalog`: the client picks an item of the tenant's catalog.

- Extract: the field gets choices — the options the bot showed last (state.last_presented),
  the items the search step found this turn and a search by the rewritten text in the
  field's categories. The extract model returns the external_id of the one the client
  named; the field value is the item title, `ref` is its external_id.
- Ask: when the field's categories have at most `max_options` items, the bot lists them
  (numbered) and they become the new last_presented, so «давайте другий» works. With
  more items the bot lists only the ones that fit what the client said (what the search
  step found, else the nearest items checked by the decider); when nothing fits, it
  names the catalog's categories instead of the items.
"""

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.core.context import TurnContext
from app.db.models import CatalogItem
from app.logging import get_logger
from app.scenarios.extract import Target
from app.scenarios.schema import FieldDef, FieldType
from app.search.base import CatalogFilters
from app.search.presented import presented
from app.search.service import Found, find_catalog, item_for, refine

if TYPE_CHECKING:
    from app.search.step import SearchOutcome
    from app.services import Services

log = get_logger(__name__)

# TODO(review): how many items the extract model chooses from
MAX_CHOICES = 20
SEARCH_CHOICES = 10
DEFAULT_MAX_OPTIONS = 5
# TODO(review): how many of the nearest items the decider checks before the bot lists them
ASK_CANDIDATES = 30
# more categories than this are not named: the question stays open
MAX_CATEGORIES = 12
OPTION_PREFIX = "opt"  # decision keys opt:<n>

# where the listed options came from (the turn trace)
ALL = "all"
SEARCH = "search"
NAMED = "named"
CATEGORIES = "categories"
OPEN = "open"


@dataclass
class AskOptions:
    """What the bot shows with the question of a catalog field."""

    items: list[Found] = field(default_factory=list)
    more: bool = False  # more items fit than are listed
    categories: list[str] = field(default_factory=list)  # named when there are no items
    source: str = OPEN

    def to_trace(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "items": [i.ref for i in self.items],
            "more": self.more,
            "categories": self.categories,
        }


def categories(f: FieldDef) -> list[str]:
    return list(f.catalog.categories) if f.catalog is not None else []


def _fits(category: str | None, cats: list[str]) -> bool:
    return not cats or category in cats


def _turn_items(ctx: TurnContext) -> list[Found]:
    found: SearchOutcome | None = ctx.extra.get("search")
    return list(found.items) if found is not None else []


async def _nearest(
    services: "Services", ctx: TurnContext, cats: list[str], rewritten: str
) -> list[Found]:
    """The items nearest to the client's words: one search per turn and set of categories
    (the extract choices and the options to list use the same candidates)."""
    cache: dict[tuple[str, ...], list[Found]] = ctx.extra.setdefault("catalog_nearest", {})
    key = tuple(cats)
    if key not in cache:
        cache[key] = await find_catalog(
            services,
            ctx.tenant_id,
            rewritten,
            filters=CatalogFilters(categories=cats),
            limit=ASK_CANDIDATES,
        )
    return cache[key]


async def catalog_choices(
    services: "Services", ctx: TurnContext, f: FieldDef, rewritten: str
) -> list[tuple[str, str]]:
    cats = categories(f)
    choices: dict[str, str] = {}
    for item in presented(ctx.state):
        if _fits(item.get("category"), cats):
            choices.setdefault(str(item["ref"]), str(item["title"]))
    for item in _turn_items(ctx):
        if _fits(item.category, cats):
            choices.setdefault(item.ref, item.title)
    for item in (await _nearest(services, ctx, cats, rewritten))[:SEARCH_CHOICES]:
        choices.setdefault(item.ref, item.title)
    return list(choices.items())[:MAX_CHOICES]


async def with_choices(
    services: "Services", ctx: TurnContext, targets: list[Target], rewritten: str
) -> tuple[list[Target], list[str]]:
    """Targets with their catalog choices. A catalog field with nothing to choose from is
    not extracted (returned in the second list): the bot asks it."""
    out: list[Target] = []
    skipped: list[str] = []
    for t in targets:
        if t.field.type != FieldType.CATALOG:
            out.append(t)
            continue
        choices = await catalog_choices(services, ctx, t.field, rewritten)
        if not choices:
            skipped.append(f"{t.scenario}.{t.field.key}")
            continue
        out.append(replace(t, choices=tuple(choices)))
    return out, skipped


async def ask_options(
    services: "Services", ctx: TurnContext, f: FieldDef, rewritten: str
) -> AskOptions:
    cats = categories(f)
    limit = f.catalog.max_options if f.catalog is not None else DEFAULT_MAX_OPTIONS
    active = [CatalogItem.tenant_id == ctx.tenant_id, CatalogItem.is_active.is_(True)]
    if cats:
        active.append(CatalogItem.category.in_(cats))
    async with services.sm() as session:
        rows = list(
            (
                await session.execute(
                    select(CatalogItem).where(*active).order_by(CatalogItem.title).limit(limit + 1)
                )
            ).scalars()
        )
    if not rows:
        return AskOptions()
    if len(rows) <= limit:
        return AskOptions([item_for(row) for row in rows], source=ALL)
    # too many to list: only what fits the client's words. The search step has already
    # checked its items
    found = [i for i in _turn_items(ctx) if _fits(i.category, cats)]
    if found:
        return AskOptions(found[:limit], more=len(found) > limit, source=SEARCH)
    # the search step did not run (the route saw no question): check the nearest items
    nearest = await _nearest(services, ctx, cats, rewritten)
    with ctx.trace.step("catalog_options", candidates=len(nearest)) as step:
        kept = await refine(
            services,
            ctx.decide_context(),
            rewritten,
            {OPTION_PREFIX: (nearest, limit + 1)},
            trace_step=step,
        )
        named = kept[OPTION_PREFIX]
        step["fit"] = [i.ref for i in named]
    if named:
        return AskOptions(named[:limit], more=len(named) > limit, source=NAMED)
    # the client named nothing from the catalog: the categories instead of the items
    async with services.sm() as session:
        found_cats = {
            c
            for c in (
                await session.execute(
                    select(CatalogItem.category).where(*active).group_by(CatalogItem.category)
                )
            ).scalars()
            if c
        }
    # the order of the field's own list, else by the alphabet
    ordered = [c for c in cats if c in found_cats] if cats else sorted(found_cats, key=str.lower)
    if 2 <= len(ordered) <= MAX_CATEGORIES:
        return AskOptions(categories=ordered, source=CATEGORIES)
    return AskOptions()


def option_label(n: int, item: Found) -> str:
    label = f"{n}. {item.title}"
    if item.price is not None:
        label += f" — {item.price:.2f} {item.currency or ''}".rstrip()
    return label
