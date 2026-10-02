"""Real Elasticsearch (`docker compose --profile search up -d elasticsearch`): the
Ukrainian and Russian analyzers, typos, the tenant and the filters. Skipped when nothing
answers at ES_TEST_URL (default http://localhost:9201)."""

import os
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from app.search.base import CatalogDoc, CatalogFilters
from app.search.es import ElasticCatalog

pytestmark = pytest.mark.search

ES_URL = os.environ.get("ES_TEST_URL", "http://localhost:9201")
TENANT = uuid.uuid4()
OTHER = uuid.uuid4()


@pytest.fixture
async def es() -> AsyncIterator[ElasticCatalog]:
    http = httpx.AsyncClient()
    try:
        up = (await http.get(ES_URL, timeout=2)).status_code == 200
    except httpx.HTTPError:
        up = False
    if not up:
        await http.aclose()
        pytest.skip(f"Elasticsearch does not answer at {ES_URL}")
    index = ElasticCatalog(http, ES_URL, prefix=f"test_{uuid.uuid4().hex[:8]}_")
    try:
        await index.upsert(DOCS)
        await index.refresh()
        yield index
    finally:
        await http.delete(f"{ES_URL}/{index.index}")
        await http.aclose()


def _doc(ref, title, text, *, language="uk", tenant=TENANT, category=None, price=None):
    return CatalogDoc(
        id=uuid.uuid5(TENANT, f"{tenant}:{ref}"),
        tenant_id=tenant,
        external_id=ref,
        title=title,
        text=f"{title}\n{text}",
        language=language,
        category=category,
        price=Decimal(price) if price is not None else None,
        attributes={},
    )


DOCS = [
    _doc("mani", "Манікюр з покриттям", "Гель-лак, догляд за кутикулою.", category="nails",
         price="450"),
    _doc("pedi", "Педикюр", "Обробка стоп і нігтів.", category="nails", price="600"),
    _doc("hair", "Жіноча стрижка", "Стрижка і укладка волосся.", category="hair", price="500"),
    _doc("ru-hair", "Стрижка мужская", "Модельная стрижка машинкой.", language="ru",
         category="hair", price="300"),
    _doc("other", "Манікюр", "Чужий тенант.", tenant=OTHER, category="nails", price="1"),
]  # fmt: skip
REF = {d.id.hex: d.external_id for d in DOCS}


async def _refs(es, query, **filters) -> list[str]:
    hits = await es.search(TENANT, query, filters=CatalogFilters(**filters), limit=10)
    return [REF[uuid.UUID(h.id).hex] for h in hits]


async def test_ukrainian_word_forms(es):
    # «манікюру», «манікюром» — the same word as «манікюр»
    assert (await _refs(es, "манікюру"))[0] == "mani"
    assert (await _refs(es, "запис на манікюром"))[0] == "mani"
    assert "pedi" not in await _refs(es, "манікюру")


async def test_typo_still_finds(es):
    assert (await _refs(es, "манікур"))[0] == "mani"


async def test_russian_analyzer(es):
    assert (await _refs(es, "мужскую стрижку"))[0] == "ru-hair"


async def test_only_the_tenant_items(es):
    assert "other" not in await _refs(es, "манікюр")


async def test_category_and_price_filters(es):
    assert set(await _refs(es, "стрижка", categories=["hair"])) == {"hair", "ru-hair"}
    assert await _refs(es, "стрижка", categories=["hair"], price_max=Decimal("400")) == ["ru-hair"]
    assert await _refs(es, "", categories=["nails"], price_min=Decimal("500")) == ["pedi"]
