"""Shared authored-text policy: trade-advice and number-word detection (UX1).

`contains_trade_instruction` / `contains_authored_quantity` are the single choke
point behind all three enforcement seams (P1 Finding schema, V5 hygiene, and the
final DTO verifier), so covering them directly covers every seam.
"""
from __future__ import annotations

import pytest

from finwatch.core.text_policy import contains_authored_quantity, contains_trade_instruction


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
    "Lost its third-largest customer",   # rank ordinal, not a magnitude
    "Management flagged going-concern doubt",
    "New segment leadership announced",
])
def test_rank_and_qualitative_prose_is_not_flagged_as_quantity(text):
    assert contains_authored_quantity(text) is False
