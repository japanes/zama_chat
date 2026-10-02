"""Search interfaces: the word index (Elasticsearch) and the vector store (Qdrant).

Both are small protocols so the turn and the jobs work with fakes in tests
(tests/search_fakes.py) and with no index at all (an empty URL in the settings).
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class SearchUnavailable(Exception):
    """The index did not answer (network, 5xx). The turn goes on without it; an index
    job retries."""


class SearchBadRequest(Exception):
    """The index rejected our request (4xx): a bug or a broken mapping, retrying will
    not help."""


@dataclass(frozen=True)
class CatalogFilters:
    categories: list[str] = field(default_factory=list)
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    # attribute → allowed values: {"color": ["red", "black"]}
    attributes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (
            self.categories
            or self.price_min is not None
            or self.price_max is not None
            or self.attributes
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.categories:
            out["categories"] = self.categories
        if self.price_min is not None:
            out["price_min"] = str(self.price_min)
        if self.price_max is not None:
            out["price_max"] = str(self.price_max)
        if self.attributes:
            out["attributes"] = self.attributes
        return out


@dataclass(frozen=True)
class IndexHit:
    id: str  # catalog item id / knowledge chunk id
    score: float


@dataclass(frozen=True)
class CatalogDoc:
    """One catalog item as the word index keeps it."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    external_id: str
    title: str
    text: str
    language: str
    category: str | None
    price: Decimal | None
    attributes: dict[str, Any]


@dataclass(frozen=True)
class Point:
    id: uuid.UUID
    vector: list[float]
    payload: dict[str, Any]


class WordIndex(Protocol):
    async def upsert(self, docs: list[CatalogDoc]) -> None: ...

    async def delete(self, ids: list[uuid.UUID]) -> None: ...

    async def delete_tenant(self, tenant_id: uuid.UUID) -> None: ...

    async def search(
        self,
        tenant_id: uuid.UUID,
        query: str,
        *,
        filters: CatalogFilters,
        limit: int,
    ) -> list[IndexHit]: ...


class VectorStore(Protocol):
    async def upsert(self, collection: str, points: list[Point]) -> None: ...

    async def delete(self, collection: str, ids: list[uuid.UUID]) -> None: ...

    async def delete_where(self, collection: str, match: dict[str, str]) -> None: ...

    async def search(
        self,
        collection: str,
        vector: list[float],
        *,
        match: dict[str, Any],
        limit: int,
    ) -> list[IndexHit]: ...


@dataclass
class SearchClients:
    """What services.extra["search"] holds. None = that index is switched off."""

    words: WordIndex | None = None
    vectors: VectorStore | None = None
    prefix: str = "zama_"

    def collection(self, kind: str, model: str) -> str:
        from app.search.text import model_slug

        return f"{self.prefix}{kind}__{model_slug(model)}"


def make_search_clients(http: Any, settings: Any) -> SearchClients:
    """From the settings; an empty URL switches that index off."""
    from app.search.es import ElasticCatalog
    from app.search.qdrant import QdrantStore

    words = None
    if settings.elasticsearch_url:
        words = ElasticCatalog(
            http,
            settings.elasticsearch_url,
            prefix=settings.search_index_prefix,
            user=settings.elasticsearch_user,
            password=settings.elasticsearch_password.get_secret_value(),
            timeout=settings.search_timeout_s,
        )
    vectors = None
    if settings.qdrant_url:
        vectors = QdrantStore(
            http,
            settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value(),
            timeout=settings.search_timeout_s,
        )
    return SearchClients(words=words, vectors=vectors, prefix=settings.search_index_prefix)
