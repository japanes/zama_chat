"""Cost of one LLM call: catalog prices per token → per 1M; cost from prices / response."""

from decimal import Decimal

import pytest

from app.llm.pricing import cost_from_prices, cost_from_response, per_million


@pytest.mark.parametrize(
    ("per_token", "expected"),
    [
        ("0.0000001", Decimal("0.1")),
        ("0.0000005", Decimal("0.5")),
        (0.000000042, Decimal("0.042")),
        ("0", Decimal("0")),
        (None, None),
        ("", None),
    ],
)
def test_per_million(per_token, expected):
    assert per_million(per_token) == expected


def _cost(**kw) -> Decimal:
    args = {
        "price_input": Decimal("0.10"),
        "price_cached_input": Decimal("0.05"),
        "price_output": Decimal("0.50"),
        "tokens_in": 1000,
        "tokens_cached": 0,
        "tokens_out": 200,
    }
    args.update(kw)
    return cost_from_prices(**args)


def test_cost_without_cache():
    # (1000 × 0.10 + 200 × 0.50) / 1M
    assert _cost() == Decimal("0.0002")


def test_cached_tokens_are_part_of_input():
    # (600 × 0.10 + 400 × 0.05 + 200 × 0.50) / 1M
    assert _cost(tokens_cached=400) == Decimal("0.00018")


def test_cached_price_missing_means_full_price():
    assert _cost(tokens_cached=400, price_cached_input=None) == _cost()


@pytest.mark.parametrize(("cached", "same_as"), [(-5, 0), (5000, 1000)])
def test_cached_count_is_clamped(cached, same_as):
    assert _cost(tokens_cached=cached) == _cost(tokens_cached=same_as)


def test_cost_from_response_order_and_bad_values():
    assert cost_from_response({}) is None
    assert cost_from_response({"usage": {"cost": 0.0123}}) == Decimal("0.0123")
    assert cost_from_response(
        {"providerMetadata": {"gateway": {"cost": "0.5"}}, "usage": {"cost": "0.1"}}
    ) == Decimal("0.1")
    assert cost_from_response({"provider_metadata": {"gateway": {"cost": "0.7"}}}) == Decimal("0.7")
    # unparsable value → the next place is tried
    assert cost_from_response(
        {"usage": {"cost": "n/a"}, "providerMetadata": {"gateway": {"cost": "0.3"}}}
    ) == Decimal("0.3")
    assert cost_from_response({"usage": {"cost": ""}}) is None
