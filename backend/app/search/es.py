"""Elasticsearch: the catalog by words (docs/04 stage 5).

One index for every tenant (`<prefix>catalog`, filtered by tenant_id). The item text
goes into the field of its language: `text_uk` (analysis-ukrainian plugin), `text_ru`,
`text_en` (built-in analyzers), any other language → `text_icu` (analysis-icu). The
query runs over all of them with typo tolerance, so "манікюру" finds "манікюр" and
"маникюр" finds the Russian item.

Plain REST through httpx — no extra client library, the same async client as the rest.
"""

import json
import uuid
from typing import Any

import httpx

from app.logging import get_logger
from app.search.base import (
    CatalogDoc,
    CatalogFilters,
    IndexHit,
    SearchBadRequest,
    SearchUnavailable,
)

log = get_logger(__name__)

LANGUAGE_FIELDS = {"uk": "text_uk", "ru": "text_ru", "en": "text_en"}
OTHER_FIELD = "text_icu"

MAPPING: dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "analysis": {
            "analyzer": {
                # titles: word boundaries for any script, case and accents folded
                "folded": {"tokenizer": "icu_tokenizer", "filter": ["icu_folding"]},
            }
        },
    },
    "mappings": {
        "dynamic": False,
        "properties": {
            "tenant_id": {"type": "keyword"},
            "external_id": {"type": "keyword"},
            "language": {"type": "keyword"},
            "category": {"type": "keyword"},
            "price": {"type": "scaled_float", "scaling_factor": 100},
            "attributes": {"type": "flattened"},
            "title": {"type": "text", "analyzer": "folded"},
            "text_uk": {"type": "text", "analyzer": "ukrainian"},
            "text_ru": {"type": "text", "analyzer": "russian"},
            "text_en": {"type": "text", "analyzer": "english"},
            "text_icu": {"type": "text", "analyzer": "icu_analyzer"},
        },
    },
}
QUERY_FIELDS = ["title^3", "text_uk", "text_ru", "text_en", "text_icu"]


def document(doc: CatalogDoc) -> dict[str, Any]:
    body: dict[str, Any] = {
        "tenant_id": str(doc.tenant_id),
        "external_id": doc.external_id,
        "language": doc.language,
        "category": doc.category,
        "price": float(doc.price) if doc.price is not None else None,
        "attributes": {k: _keyword(v) for k, v in (doc.attributes or {}).items()},
        "title": doc.title,
    }
    body[LANGUAGE_FIELDS.get(doc.language, OTHER_FIELD)] = doc.text
    return body


def _keyword(value: Any) -> Any:
    # flattened fields keep keywords: numbers and booleans as strings
    if isinstance(value, list):
        return [_keyword(v) for v in value]
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def search_body(
    tenant_id: uuid.UUID, query: str, filters: CatalogFilters, limit: int
) -> dict[str, Any]:
    flt: list[dict[str, Any]] = [{"term": {"tenant_id": str(tenant_id)}}]
    if filters.categories:
        flt.append({"terms": {"category": filters.categories}})
    price: dict[str, float] = {}
    if filters.price_min is not None:
        price["gte"] = float(filters.price_min)
    if filters.price_max is not None:
        price["lte"] = float(filters.price_max)
    if price:
        flt.append({"range": {"price": price}})
    for key, values in filters.attributes.items():
        flt.append({"terms": {f"attributes.{key}": [_keyword(v) for v in values]}})
    must: dict[str, Any]
    if query.strip():
        must = {
            "multi_match": {
                "query": query,
                "fields": QUERY_FIELDS,
                "type": "best_fields",
                "fuzziness": "AUTO",
                "prefix_length": 1,
            }
        }
    else:
        must = {"match_all": {}}
    return {"size": limit, "_source": False, "query": {"bool": {"filter": flt, "must": must}}}


class ElasticCatalog:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        *,
        prefix: str = "zama_",
        user: str = "",
        password: str = "",
        timeout: float = 10.0,
    ) -> None:
        self.http = http
        self.url = url.rstrip("/")
        self.index = f"{prefix}catalog"
        self.auth = (user, password) if user else None
        self.timeout = timeout
        self._ready = False

    async def _call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        content: str | None = None,
        ok: tuple[int, ...] = (),
    ) -> httpx.Response:
        headers = {}
        if content is not None:
            headers["Content-Type"] = "application/x-ndjson"
        try:
            resp = await self.http.request(
                method,
                f"{self.url}{path}",
                json=body if content is None else None,
                content=content,
                headers=headers,
                auth=self.auth,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise SearchUnavailable(f"elasticsearch: {type(exc).__name__}") from None
        if resp.status_code in ok or resp.status_code < 300:
            return resp
        text = resp.text[:300]
        if resp.status_code >= 500 or resp.status_code == 429:
            raise SearchUnavailable(f"elasticsearch {resp.status_code}: {text}")
        raise SearchBadRequest(f"elasticsearch {resp.status_code}: {text}")

    async def ensure_index(self) -> None:
        if self._ready:
            return
        resp = await self._call("HEAD", f"/{self.index}", ok=(404,))
        if resp.status_code == 404:
            resp = await self._call("PUT", f"/{self.index}", body=MAPPING, ok=(400,))
            # created at the same moment by another worker
            if resp.status_code == 400 and "resource_already_exists" not in resp.text:
                raise SearchBadRequest(f"elasticsearch: {resp.text[:300]}")
            log.info("search.es_index_created", index=self.index)
        self._ready = True

    async def upsert(self, docs: list[CatalogDoc]) -> None:
        if not docs:
            return
        await self.ensure_index()
        lines = []
        for doc in docs:
            lines.append(json.dumps({"index": {"_index": self.index, "_id": str(doc.id)}}))
            lines.append(json.dumps(document(doc), ensure_ascii=False))
        resp = await self._call("POST", "/_bulk", content="\n".join(lines) + "\n")
        data = resp.json()
        if data.get("errors"):
            bad = [i for i in data.get("items", []) if i.get("index", {}).get("error")]
            reason = bad[0]["index"]["error"] if bad else "unknown"
            raise SearchBadRequest(f"elasticsearch bulk: {json.dumps(reason)[:300]}")

    async def delete(self, ids: list[uuid.UUID]) -> None:
        if not ids:
            return
        await self.ensure_index()
        lines = [json.dumps({"delete": {"_index": self.index, "_id": str(i)}}) for i in ids]
        await self._call("POST", "/_bulk", content="\n".join(lines) + "\n")

    async def delete_tenant(self, tenant_id: uuid.UUID) -> None:
        await self.ensure_index()
        await self._call(
            "POST",
            f"/{self.index}/_delete_by_query?conflicts=proceed",
            body={"query": {"term": {"tenant_id": str(tenant_id)}}},
        )

    async def refresh(self) -> None:
        """Make the last writes searchable now (tests; ES does it every second anyway)."""
        await self.ensure_index()
        await self._call("POST", f"/{self.index}/_refresh")

    async def search(
        self,
        tenant_id: uuid.UUID,
        query: str,
        *,
        filters: CatalogFilters,
        limit: int,
    ) -> list[IndexHit]:
        await self.ensure_index()
        resp = await self._call(
            "POST", f"/{self.index}/_search", body=search_body(tenant_id, query, filters, limit)
        )
        hits = resp.json().get("hits", {}).get("hits", [])
        return [IndexHit(h["_id"], float(h.get("_score") or 0.0)) for h in hits]

    async def ping(self) -> dict[str, Any]:
        resp = await self._call("GET", "/")
        return resp.json()
