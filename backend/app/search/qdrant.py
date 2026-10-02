"""Qdrant: the catalog and the knowledge base by meaning (docs/04 stage 5).

A collection per kind and embedding model: `<prefix>catalog__<model>` and
`<prefix>knowledge__<model>`. A new embedding model gets new, empty collections, so
vectors of different models never mix; the admin shows "re-index needed" and the
re-index job fills them. Every point has tenant_id in its payload (indexed field).

Plain REST through httpx.
"""

import uuid
from typing import Any

import httpx

from app.logging import get_logger
from app.search.base import IndexHit, Point, SearchBadRequest, SearchUnavailable

log = get_logger(__name__)

PAYLOAD_INDEXES = ("tenant_id", "language", "source_id")


def match_filter(match: dict[str, Any]) -> dict[str, Any]:
    must = []
    for key, value in match.items():
        if isinstance(value, (list, tuple)):
            must.append({"key": key, "match": {"any": [str(v) for v in value]}})
        else:
            must.append({"key": key, "match": {"value": str(value)}})
    return {"must": must}


class QdrantStore:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        *,
        api_key: str = "",
        timeout: float = 10.0,
    ) -> None:
        self.http = http
        self.url = url.rstrip("/")
        self.headers = {"api-key": api_key} if api_key else {}
        self.timeout = timeout
        self._ready: dict[str, int] = {}

    async def _call(
        self, method: str, path: str, body: Any = None, *, ok: tuple[int, ...] = ()
    ) -> httpx.Response:
        try:
            resp = await self.http.request(
                method,
                f"{self.url}{path}",
                json=body,
                headers=self.headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise SearchUnavailable(f"qdrant: {type(exc).__name__}") from None
        if resp.status_code in ok or resp.status_code < 300:
            return resp
        text = resp.text[:300]
        if resp.status_code >= 500 or resp.status_code == 429:
            raise SearchUnavailable(f"qdrant {resp.status_code}: {text}")
        raise SearchBadRequest(f"qdrant {resp.status_code}: {text}")

    async def exists(self, collection: str) -> bool:
        if collection in self._ready:
            return True
        resp = await self._call("GET", f"/collections/{collection}", ok=(404,))
        return resp.status_code != 404

    async def ensure(self, collection: str, size: int) -> None:
        if self._ready.get(collection) == size:
            return
        resp = await self._call("GET", f"/collections/{collection}", ok=(404,))
        if resp.status_code == 404:
            await self._call(
                "PUT",
                f"/collections/{collection}",
                {"vectors": {"size": size, "distance": "Cosine"}},
                ok=(409,),
            )
            for name in PAYLOAD_INDEXES:
                await self._call(
                    "PUT",
                    f"/collections/{collection}/index?wait=true",
                    {"field_name": name, "field_schema": "keyword"},
                )
            log.info("search.qdrant_collection_created", collection=collection, size=size)
        else:
            have = (
                resp.json()
                .get("result", {})
                .get("config", {})
                .get("params", {})
                .get("vectors", {})
                .get("size")
            )
            if have is not None and int(have) != size:
                raise SearchBadRequest(
                    f"qdrant: collection {collection} has vectors of size {have}, not {size}"
                )
        self._ready[collection] = size

    async def upsert(self, collection: str, points: list[Point]) -> None:
        if not points:
            return
        await self.ensure(collection, len(points[0].vector))
        await self._call(
            "PUT",
            f"/collections/{collection}/points?wait=true",
            {
                "points": [
                    {"id": str(p.id), "vector": p.vector, "payload": p.payload} for p in points
                ]
            },
        )

    async def delete(self, collection: str, ids: list[uuid.UUID]) -> None:
        if not ids or not await self.exists(collection):
            return
        await self._call(
            "POST",
            f"/collections/{collection}/points/delete?wait=true",
            {"points": [str(i) for i in ids]},
        )

    async def delete_where(self, collection: str, match: dict[str, str]) -> None:
        if not await self.exists(collection):
            return
        await self._call(
            "POST",
            f"/collections/{collection}/points/delete?wait=true",
            {"filter": match_filter(match)},
        )

    async def search(
        self,
        collection: str,
        vector: list[float],
        *,
        match: dict[str, Any],
        limit: int,
    ) -> list[IndexHit]:
        resp = await self._call(
            "POST",
            f"/collections/{collection}/points/query",
            {"query": vector, "filter": match_filter(match), "limit": limit},
            ok=(404,),
        )
        if resp.status_code == 404:  # nothing indexed with this model yet
            return []
        points = resp.json().get("result", {}).get("points", [])
        return [IndexHit(str(p["id"]), float(p.get("score") or 0.0)) for p in points]

    async def ping(self) -> dict[str, Any]:
        resp = await self._call("GET", "/collections")
        return {"collections": len(resp.json().get("result", {}).get("collections", []))}
