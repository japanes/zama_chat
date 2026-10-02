"""Vercel AI Gateway model catalog (GET /v1/models).

Used by "add model" in the admin and `zama models add NAME --from-gateway`: prices,
context window and capabilities are filled in from the catalog, the admin can edit
them before saving.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from app.errors import AppError
from app.llm.pricing import per_million


@dataclass
class CatalogModel:
    name: str
    label: str
    kind: str  # language | embedding | evaluation | image | …
    price_input: Decimal
    price_cached_input: Decimal | None
    price_output: Decimal
    context_window: int | None
    max_output: int | None
    supports_json_schema: bool
    supports_reasoning: bool
    supports_vision: bool
    reasoning_efforts: list[str] = field(default_factory=list)
    zdr: str | None = None
    description: str = ""

    def model_fields(self) -> dict[str, Any]:
        """What an llm_models row takes from the catalog (the admin may edit it)."""
        return {
            "label": self.label,
            "kind": "embedding" if self.kind == "embedding" else "language",
            "price_input": self.price_input,
            "price_cached_input": self.price_cached_input,
            "price_output": self.price_output,
            "context_window": self.context_window,
            "max_output": self.max_output,
            "supports_json_schema": self.supports_json_schema,
            "supports_reasoning": self.supports_reasoning,
            "supports_vision": self.supports_vision,
            "reasoning_efforts": self.reasoning_efforts,
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("price_input", "price_cached_input", "price_output"):
            if data[key] is not None:
                data[key] = str(data[key])
        return data


def parse_catalog_entry(entry: dict[str, Any]) -> CatalogModel:
    pricing = entry.get("pricing") or {}
    tags = set(entry.get("tags") or [])
    params = set(entry.get("supported_parameters") or [])
    inputs = set((entry.get("modalities") or {}).get("input") or [])
    efforts: list[str] = []
    for option in entry.get("reasoning_options") or []:
        if option.get("type") == "effort":
            efforts = [str(v) for v in option.get("values") or []]
    return CatalogModel(
        name=entry["id"],
        label=entry.get("name") or entry["id"],
        kind=entry.get("type") or "language",
        price_input=per_million(pricing.get("input")) or Decimal(0),
        price_cached_input=per_million(pricing.get("input_cache_read")),
        price_output=per_million(pricing.get("output")) or Decimal(0),
        context_window=entry.get("context_window") or None,
        max_output=entry.get("max_tokens") or None,
        supports_json_schema=bool(
            "structured-output" in tags or {"response_format", "structured_outputs"} & params
        ),
        supports_reasoning="reasoning" in tags or "reasoning" in params,
        supports_vision="vision" in tags or "image" in inputs,
        reasoning_efforts=efforts,
        zdr=entry.get("zdr"),
        description=entry.get("description") or "",
    )


class GatewayCatalog:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def list_models(self, kind: str | None = None) -> list[CatalogModel]:
        try:
            resp = await self.http.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                timeout=20.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AppError("models.catalog_unavailable", {"error": type(exc).__name__}) from exc
        models = [parse_catalog_entry(e) for e in resp.json().get("data") or [] if e.get("id")]
        if kind:
            models = [m for m in models if m.kind == kind]
        return sorted(models, key=lambda m: m.name)

    async def get(self, name: str) -> CatalogModel:
        for model in await self.list_models():
            if model.name == name:
                return model
        raise AppError("models.not_in_catalog", {"name": name}, status=404)
