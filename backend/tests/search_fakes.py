"""In-memory stand-ins for Elasticsearch and Qdrant (app/search/base.py protocols).

FakeWordIndex matches words by a common stem (the first 5 letters), enough to tell
"манікюр" from "педикюр" and to find "манікюру" by "манікюр". FakeVectorStore ranks by
cosine similarity; `match` works like the Qdrant filter (a list = any of).
Set `fail` to an exception to make every call raise it.
"""

import math
import re
import uuid
from typing import Any

from app.db.base import new_id
from app.db.models import CatalogItem, KnowledgeSource
from app.search.base import CatalogDoc, CatalogFilters, IndexHit, Point, SearchClients
from app.search.index import queue_item, queue_source

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
STEM = 5


def _stems(text: str) -> set[str]:
    return {w.lower()[:STEM] for w in _WORD.findall(text) if len(w) >= 3}


class FakeWordIndex:
    def __init__(self) -> None:
        self.docs: dict[uuid.UUID, CatalogDoc] = {}
        self.fail: BaseException | None = None
        self.searches: list[dict[str, Any]] = []

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    async def ping(self) -> dict[str, Any]:
        self._check()
        return {"docs": len(self.docs)}

    async def upsert(self, docs: list[CatalogDoc]) -> None:
        self._check()
        for doc in docs:
            self.docs[doc.id] = doc

    async def delete(self, ids: list[uuid.UUID]) -> None:
        self._check()
        for i in ids:
            self.docs.pop(i, None)

    async def delete_tenant(self, tenant_id: uuid.UUID) -> None:
        self._check()
        self.docs = {k: d for k, d in self.docs.items() if d.tenant_id != tenant_id}

    async def search(
        self, tenant_id: uuid.UUID, query: str, *, filters: CatalogFilters, limit: int
    ) -> list[IndexHit]:
        self._check()
        self.searches.append({"tenant_id": tenant_id, "query": query, "filters": filters})
        wanted = _stems(query)
        hits = []
        for doc in self.docs.values():
            if doc.tenant_id != tenant_id:
                continue
            if filters.categories and doc.category not in filters.categories:
                continue
            score = len(wanted & _stems(f"{doc.title} {doc.text}"))
            if score or not wanted:
                hits.append(IndexHit(str(doc.id), float(score)))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb)


def _matches(payload: dict[str, Any], match: dict[str, Any]) -> bool:
    for key, value in match.items():
        allowed = {str(v) for v in value} if isinstance(value, list | tuple) else {str(value)}
        if str(payload.get(key)) not in allowed:
            return False
    return True


class FakeVectorStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[uuid.UUID, Point]] = {}
        self.fail: BaseException | None = None
        self.searches: list[dict[str, Any]] = []

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    async def ping(self) -> dict[str, Any]:
        self._check()
        return {"collections": len(self.collections)}

    def points(self, collection: str) -> list[Point]:
        return list(self.collections.get(collection, {}).values())

    async def upsert(self, collection: str, points: list[Point]) -> None:
        self._check()
        store = self.collections.setdefault(collection, {})
        for p in points:
            store[p.id] = p

    async def delete(self, collection: str, ids: list[uuid.UUID]) -> None:
        self._check()
        store = self.collections.get(collection, {})
        for i in ids:
            store.pop(i, None)

    async def delete_where(self, collection: str, match: dict[str, str]) -> None:
        self._check()
        store = self.collections.get(collection, {})
        for key in [k for k, p in store.items() if _matches(p.payload, match)]:
            del store[key]

    async def search(
        self, collection: str, vector: list[float], *, match: dict[str, Any], limit: int
    ) -> list[IndexHit]:
        self._check()
        self.searches.append({"collection": collection, "match": dict(match)})
        hits = [
            IndexHit(str(p.id), _cosine(vector, p.vector))
            for p in self.collections.get(collection, {}).values()
            if _matches(p.payload, match)
        ]
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


def fake_search(
    services: Any, *, words: bool = True, vectors: bool = True
) -> tuple[FakeWordIndex | None, FakeVectorStore | None]:
    """Put fakes into services.extra["search"]; returns them for the asserts."""
    w = FakeWordIndex() if words else None
    v = FakeVectorStore() if vectors else None
    services.extra["search"] = SearchClients(words=w, vectors=v, prefix="test_")
    return w, v


# --- rows + their index jobs (run them with Worker.run_due()) ---------------------------


async def add_item(services, tenant_id, external_id, title, **kw) -> CatalogItem:
    async with services.sm() as session, session.begin():
        item = CatalogItem(
            id=new_id(),
            tenant_id=tenant_id,
            external_id=external_id,
            title=title,
            description=kw.pop("description", ""),
            attributes=kw.pop("attributes", {}),
            language=kw.pop("language", "uk"),
            is_active=kw.pop("is_active", True),
            **kw,
        )
        session.add(item)
        await queue_item(session, item, services.clock.now(), force=True)
    return item


async def change_item(services, item_id, *, force=False, **changes) -> None:
    async with services.sm() as session, session.begin():
        item = await session.get(CatalogItem, item_id)
        for key, value in changes.items():
            setattr(item, key, value)
        await queue_item(session, item, services.clock.now(), force=force)


async def add_source(services, tenant_id, title, body, **kw) -> KnowledgeSource:
    async with services.sm() as session, session.begin():
        source = KnowledgeSource(
            id=new_id(),
            tenant_id=tenant_id,
            title=title,
            body=body,
            language=kw.pop("language", "uk"),
            is_active=kw.pop("is_active", True),
            **kw,
        )
        session.add(source)
        await queue_source(session, source, services.clock.now(), force=True)
    return source
