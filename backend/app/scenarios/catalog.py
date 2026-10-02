"""Scenario fields of type `catalog`: the client picks an item of the tenant's catalog.

- Extract: the field gets choices — the options the bot showed last (state.last_presented),
  the items the search step found this turn and a search by the rewritten text in the
  field's categories. The extract model returns the external_id of the one the client
  named; the field value is the item title, `ref` is its external_id.
- Ask: when the field's categories have at most `max_options` items, the bot lists them
  (numbered) and they become the new last_presented, so «давайте другий» works. With
  more items the bot asks an open question (plus what the search found this turn).
"""

from dataclasses import replace
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.context import TurnContext
from app.db.models import CatalogItem
from app.logging import get_logger
from app.scenarios.extract import Target
from app.scenarios.schema import FieldDef, FieldType
from app.search.base import CatalogFilters
from app.search.presented import presented
from app.search.service import Found, find_catalog, item_for

if TYPE_CHECKING:
    from app.search.step import SearchOutcome
    from app.services import Services

log = get_logger(__name__)

# TODO(review): how many items the extract model chooses from
MAX_CHOICES = 20
SEARCH_CHOICES = 10
DEFAULT_MAX_OPTIONS = 5


def categories(f: FieldDef) -> list[str]:
    return list(f.catalog.categories) if f.catalog is not None else []


def _fits(category: str | None, cats: list[str]) -> bool:
    return not cats or category in cats


def _turn_items(ctx: TurnContext) -> list[Found]:
    found: SearchOutcome | None = ctx.extra.get("search")
    return list(found.items) if found is not None else []


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
    hits = await find_catalog(
        services,
        ctx.tenant_id,
        rewritten,
        filters=CatalogFilters(categories=cats),
        limit=SEARCH_CHOICES,
    )
    for item in hits:
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


async def ask_options(services: "Services", ctx: TurnContext, f: FieldDef) -> list[Found]:
    cats = categories(f)
    limit = f.catalog.max_options if f.catalog is not None else DEFAULT_MAX_OPTIONS
    query = select(CatalogItem).where(
        CatalogItem.tenant_id == ctx.tenant_id, CatalogItem.is_active.is_(True)
    )
    if cats:
        query = query.where(CatalogItem.category.in_(cats))
    async with services.sm() as session:
        rows = list(
            (await session.execute(query.order_by(CatalogItem.title).limit(limit + 1))).scalars()
        )
    if rows and len(rows) <= limit:
        return [item_for(row) for row in rows]
    # too many to list: what the search found this turn, if anything
    return [i for i in _turn_items(ctx) if _fits(i.category, cats)][:limit]


def option_label(n: int, item: Found) -> str:
    label = f"{n}. {item.title}"
    if item.price is not None:
        label += f" — {item.price:.2f} {item.currency or ''}".rstrip()
    return label
