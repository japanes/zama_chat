"""Pure search helpers: item text and hash, knowledge chunks, point ids, RRF, the ES
request body, the Qdrant filter, last_presented."""

import uuid
from dataclasses import replace
from decimal import Decimal

from app.db.models import CatalogItem, KnowledgeSource
from app.search.base import CatalogDoc, CatalogFilters, IndexHit
from app.search.es import document, search_body
from app.search.presented import STATE_KEY, presented, rewrite_view, with_presented
from app.search.qdrant import match_filter
from app.search.service import Found, candidate_question, rrf
from app.search.text import (
    CHUNK_CHARS,
    chunk_id,
    chunk_text,
    item_hash,
    item_text,
    model_slug,
    source_chunks,
    source_hash,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _item(**kw) -> CatalogItem:
    data = {
        "tenant_id": TENANT,
        "external_id": "sku-1",
        "title": "Манікюр з покриттям",
        "description": "Гель-лак, догляд за кутикулою.",
        "category": "manicure",
        "price": Decimal("450.00"),
        "currency": "UAH",
        "url": None,
        "image_url": None,
        "attributes": {"duration": 90, "colors": ["red", "nude"]},
        "language": "uk",
        "is_active": True,
    }
    data.update(kw)
    return CatalogItem(**data)


# --- item text and hash ---------------------------------------------------------------


def test_item_text_has_title_category_description_and_attributes():
    text = item_text(_item())
    lines = text.split("\n")
    assert lines[0] == "Манікюр з покриттям"
    assert lines[1] == "manicure"
    assert "Гель-лак" in lines[2]
    # attributes sorted by name, lists joined
    assert lines[3:] == ["colors: red, nude", "duration: 90"]


def test_item_hash_follows_what_the_indexes_keep():
    base = item_hash(_item())
    assert item_hash(_item()) == base
    assert item_hash(_item(price=Decimal("500"))) != base
    assert item_hash(_item(is_active=False)) != base
    assert item_hash(_item(attributes={"duration": 60})) != base
    # 450 and 450.00 are the same price
    assert item_hash(_item(price=Decimal("450"))) == base
    # the index bookkeeping is not content
    assert item_hash(_item(index_status="error", index_error="x")) == base


def test_source_hash_changes_with_the_body():
    a = KnowledgeSource(tenant_id=TENANT, title="Доставка", body="1", language="uk")
    b = KnowledgeSource(tenant_id=TENANT, title="Доставка", body="2", language="uk")
    a.is_active = b.is_active = True
    assert source_hash(a) != source_hash(b)


# --- chunks -----------------------------------------------------------------------------


def test_short_text_is_one_chunk():
    assert chunk_text("Доставка Новою поштою.\n\nОплата при отриманні.") == [
        "Доставка Новою поштою. Оплата при отриманні."
    ]


def test_long_text_is_split_by_paragraphs_with_overlap():
    paragraphs = [f"Абзац {n}. " + "слово " * 30 for n in range(10)]
    chunks = chunk_text("\n\n".join(paragraphs), max_chars=400, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)
    # every paragraph start is in some chunk
    for n in range(10):
        assert any(f"Абзац {n}." in c for c in chunks)
    # the next chunk starts with the end of the previous one (at a word boundary)
    tail = chunks[1].split(" Абзац")[0]
    assert 0 < len(tail) <= 50
    assert chunks[0].endswith(" " + tail)


def test_a_sentence_longer_than_the_limit_is_cut():
    chunks = chunk_text("а" * 1000, max_chars=300, overlap=0)
    assert all(len(c) <= 300 for c in chunks)
    assert "".join(chunks) == "а" * 1000


def test_source_chunks_start_with_the_title():
    source = KnowledgeSource(
        tenant_id=TENANT, title="Повернення", body="Протягом 14 днів.", language="uk"
    )
    assert source_chunks(source) == ["Повернення\nПротягом 14 днів."]


def test_chunk_ids_are_stable_and_depend_on_the_text():
    sid = uuid.uuid4()
    assert chunk_id(sid, 0, "a") == chunk_id(sid, 0, "a")
    assert chunk_id(sid, 0, "a") != chunk_id(sid, 0, "b")
    assert chunk_id(sid, 0, "a") != chunk_id(sid, 1, "a")


def test_model_slug():
    assert model_slug("openai/text-embedding-3-large") == "openai_text_embedding_3_large"


# --- merge ------------------------------------------------------------------------------


def test_rrf_puts_an_item_found_by_both_first():
    words = [IndexHit("a", 9), IndexHit("b", 5), IndexHit("c", 1)]
    vectors = [IndexHit("c", 0.9), IndexHit("d", 0.8)]
    order = rrf([words, vectors])
    assert order[0] == "c"
    assert set(order) == {"a", "b", "c", "d"}
    # a single list keeps its order
    assert rrf([words]) == ["a", "b", "c"]


# --- Elasticsearch body / Qdrant filter -------------------------------------------------


def test_es_document_puts_the_text_into_the_language_field():
    doc = CatalogDoc(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        external_id="sku-1",
        title="Манікюр",
        text="Манікюр\nгель-лак",
        language="uk",
        category="manicure",
        price=Decimal("450"),
        attributes={"duration": 90, "gel": True},
    )
    body = document(doc)
    assert body["text_uk"] == "Манікюр\nгель-лак"
    assert "text_en" not in body
    assert body["price"] == 450.0
    assert body["attributes"] == {"duration": "90", "gel": "true"}
    other = document(replace(doc, language="pl"))
    assert "text_icu" in other


def test_es_search_body_filters_by_tenant_and_conditions():
    filters = CatalogFilters(
        categories=["manicure"], price_max=Decimal("500"), attributes={"color": ["red"]}
    )
    body = search_body(TENANT, "манікюр", filters, 50)
    flt = body["query"]["bool"]["filter"]
    assert {"term": {"tenant_id": str(TENANT)}} in flt
    assert {"terms": {"category": ["manicure"]}} in flt
    assert {"range": {"price": {"lte": 500.0}}} in flt
    assert {"terms": {"attributes.color": ["red"]}} in flt
    must = body["query"]["bool"]["must"]["multi_match"]
    assert must["query"] == "манікюр"
    assert must["fuzziness"] == "AUTO"
    assert body["size"] == 50


def test_es_empty_query_matches_all():
    body = search_body(TENANT, "  ", CatalogFilters(), 10)
    assert body["query"]["bool"]["must"] == {"match_all": {}}


def test_qdrant_match_filter():
    assert match_filter({"tenant_id": "t", "category": ["a", "b"]}) == {
        "must": [
            {"key": "tenant_id", "match": {"value": "t"}},
            {"key": "category", "match": {"any": ["a", "b"]}},
        ]
    }


# --- last_presented ---------------------------------------------------------------------


def test_presented_round_trip():
    found = Found(
        kind="catalog",
        id="1",
        ref="sku-1",
        title="Манікюр",
        text="",
        language="uk",
        price=Decimal("450"),
        currency="UAH",
    )
    state = with_presented({"other": 1}, [found.to_presented(1)], at="2026-10-02T10:00:00")
    assert state["other"] == 1
    assert state[f"{STATE_KEY}_at"] == "2026-10-02T10:00:00"
    assert presented(state) == [
        {"n": 1, "ref": "sku-1", "title": "Манікюр", "price": "450.00", "currency": "UAH"}
    ]
    assert rewrite_view(presented(state)) == [{"n": 1, "title": "Манікюр", "price": "450.00 UAH"}]


def test_presented_ignores_broken_state():
    assert presented(None) == []
    assert presented({STATE_KEY: "x"}) == []
    assert presented({STATE_KEY: [{"n": 1}, {"ref": "a", "title": "A"}]}) == [
        {"ref": "a", "title": "A"}
    ]


def test_context_entries():
    item = Found(
        kind="catalog",
        id="1",
        ref="sku-1",
        title="Манікюр",
        text="Гель-лак",
        language="uk",
        category="manicure",
        price=Decimal("450"),
        currency="UAH",
    )
    assert item.to_context(2) == {
        "kind": "item",
        "n": 2,
        "title": "Манікюр",
        "category": "manicure",
        "description": "Гель-лак",
        "price": "450.00",
        "currency": "UAH",
    }
    kb = Found(kind="knowledge", id="2", ref="s", title="Доставка", text="НП", language="uk")
    assert kb.to_context() == {"kind": "knowledge", "title": "Доставка", "text": "НП"}


def test_the_decider_sees_a_whole_knowledge_chunk():
    # the answer is in the last words of a chunk of the largest size
    answer = "Поруч є платна парковка."
    body = ("слово " * CHUNK_CHARS)[: CHUNK_CHARS - len(answer) - 1] + " " + answer
    source = KnowledgeSource(tenant_id=TENANT, title="Правила салону", body=body, language="uk")
    (chunk,) = source_chunks(source)
    found = Found(kind="knowledge", id="1", ref="1", title=source.title, text=chunk, language="uk")
    text = candidate_question("kb:1", found).instructions
    assert text.endswith(answer)
    assert text.count("Правила салону") == 1
