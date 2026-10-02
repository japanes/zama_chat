"""Cost of one LLM call.

Prices live in `llm_models` (USD per 1M tokens, edited in the admin). The Gateway
catalog gives per-token strings ("0.0000001") — `per_million()` converts them.
"""

from decimal import Decimal
from typing import Any

MILLION = Decimal(1_000_000)


def per_million(per_token: str | float | None) -> Decimal | None:
    if per_token in (None, ""):
        return None
    return (Decimal(str(per_token)) * MILLION).normalize()


def cost_from_prices(
    *,
    price_input: Decimal,
    price_cached_input: Decimal | None,
    price_output: Decimal,
    tokens_in: int,
    tokens_cached: int,
    tokens_out: int,
) -> Decimal:
    """tokens_in includes cached tokens (OpenAI usage semantics)."""
    cached = min(max(tokens_cached, 0), max(tokens_in, 0))
    cached_price = price_input if price_cached_input is None else price_cached_input
    total = (
        Decimal(tokens_in - cached) * price_input
        + Decimal(cached) * cached_price
        + Decimal(tokens_out) * price_output
    )
    return total / MILLION


def cost_from_response(raw: dict[str, Any]) -> Decimal | None:
    """Cost reported by the Gateway itself, if the response carries it.

    TODO(review): the Gateway docs do not say that chat completions return the cost
    (only GET /v1/generation does, asynchronously). We look in the places it may show
    up; without it the cost is computed from `llm_models` prices. Check on the probe.
    """
    candidates = [
        (raw.get("usage") or {}).get("cost"),
        ((raw.get("providerMetadata") or {}).get("gateway") or {}).get("cost"),
        ((raw.get("provider_metadata") or {}).get("gateway") or {}).get("cost"),
    ]
    for value in candidates:
        if value not in (None, ""):
            try:
                return Decimal(str(value))
            except ArithmeticError:
                continue
    return None
