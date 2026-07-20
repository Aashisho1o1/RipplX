"""Adversarial coverage for the shared authored-headline policy."""
from __future__ import annotations

import pytest

from finwatch.core.text_policy import (
    authored_text_violations,
    contains_authored_quantity,
    contains_trade_instruction,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "We estimate a fair value near a high-teens price target and recommend buying",
            [
                "quantity",
                "trade_instruction",
                "price_target",
                "first_person_valuation",
            ],
        ),
        ("Guaranteed and obvious upside", ["forbidden_vocabulary"]),
    ],
)
def test_authored_text_violations_have_stable_policy_order(text, expected):
    assert authored_text_violations(text) == expected


@pytest.mark.parametrize("text", [
    "I recommend buying the shares",
    "Investors should consider adding shares",
    "Buying shares looks attractive",
    "We recommend accumulating the position",
    "You should sell before the print",
    "Shareholders should reduce exposure",
    "consider adding to the position",
    "We recommend avoiding the stock",   # original policy must still hold
    "You should avoid the stock",
])
def test_trade_advice_is_flagged(text):
    assert contains_trade_instruction(text) is True


@pytest.mark.parametrize("text", [
    "Auditor recommended restating prior results",   # descriptive, not a trade rec
    "The board is considering a new facility",
    "Management withdrew full-year guidance",
    "New segment leadership announced",
])
def test_descriptive_prose_is_not_flagged_as_trade_advice(text):
    assert contains_trade_instruction(text) is False


@pytest.mark.parametrize("text", [
    "Revenue quadrupled",
    "Backlog grew fourfold",
    "Costs rose twice as fast",
    "Roughly a third of revenue is at risk",
    "Margins doubled",                   # original policy must still hold
    "Guidance rose fifty percent",
])
def test_number_words_are_flagged(text):
    assert contains_authored_quantity(text) is True


@pytest.mark.parametrize("text", [
    "Margins moved ½ a point",
    "The ² adjustment changed",
    "Exposure moved Ⅳ times",
    "Margins moved by 25 bps",
    "Growth reached the low single-digits",
    "Growth reached the mid-teens",
    "Growth reached the high tens",
])
def test_unicode_numeric_glyphs_bps_and_financial_bands_are_quantities(text):
    assert contains_authored_quantity(text) is True


@pytest.mark.parametrize("text", [
    "Lost its third-largest customer",   # rank ordinal, not a magnitude
    "Management flagged going-concern doubt",
    "New segment leadership announced",
])
def test_rank_and_qualitative_prose_is_not_flagged_as_quantity(text):
    assert contains_authored_quantity(text) is False


@pytest.mark.parametrize("text", [
    "Honeymoon demand normalized",
    "The mooncake segment was sold",
    "Obviously different controls were disclosed",
])
def test_forbidden_vocabulary_uses_token_boundaries(text):
    assert "forbidden_vocabulary" not in authored_text_violations(text)


@pytest.mark.parametrize("text", ["The moon thesis", "The outcome is obvious"])
def test_standalone_forbidden_vocabulary_remains_blocked(text):
    assert "forbidden_vocabulary" in authored_text_violations(text)


def test_first_person_valuation_is_narrowly_scoped():
    assert "first_person_valuation" in authored_text_violations(
        "We estimate a fair value for the shares"
    )
    assert authored_text_violations("Fair value measurement controls changed") == []
