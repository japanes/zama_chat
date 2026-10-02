"""Operator mode: how many variants and how they are read from the model's answer."""

from app.operator.variants import (
    DEFAULT_VARIANTS,
    MAX_VARIANTS,
    MIN_VARIANTS,
    instruction,
    parse,
    schema,
    variant_count,
)


def test_variant_count_from_channel_settings():
    assert variant_count({}) == DEFAULT_VARIANTS
    assert variant_count({"operator_variants": 4}) == 4
    assert variant_count({"operator_variants": 1}) == MIN_VARIANTS
    assert variant_count({"operator_variants": 99}) == MAX_VARIANTS
    # not a number → the default
    assert variant_count({"operator_variants": "4"}) == DEFAULT_VARIANTS
    assert variant_count({"operator_variants": True}) == DEFAULT_VARIANTS


def test_structured_answer():
    data = {"variants": ["Так, є вільне вікно.", "  ", "Так, є вільне вікно.", "Є, о 15:00."]}
    assert parse("", data, 3) == ["Так, є вільне вікно.", "Є, о 15:00."]


def test_json_in_text():
    text = '{"variants": ["A", "B", "C", "D"]}'
    assert parse(text, None, 3) == ["A", "B", "C"]


def test_text_split_by_dashes_and_numbering_removed():
    text = "1. Добрий день!\n---\n2) Вітаю!\n\n -----\nVariant 3: Привіт!"
    assert parse(text, None, 3) == ["Добрий день!", "Вітаю!", "Привіт!"]


def test_one_variant_is_better_than_none():
    assert parse("Just one reply", None, 3) == ["Just one reply"]
    assert parse("   ", None, 3) == []
    # broken JSON is still text
    assert parse('{"variants": [', None, 3) == ['{"variants": [']


def test_numbers_inside_the_text_stay():
    assert parse("Manicure costs 600 UAH.\n2 hours.", None, 3) == [
        "Manicure costs 600 UAH.\n2 hours."
    ]


def test_schema_and_instruction():
    assert schema(4)["properties"]["variants"]["maxItems"] == 4
    assert "4 different variants" in instruction(4, with_schema=True)
    assert "---" in instruction(3, with_schema=False)
