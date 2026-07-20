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


@pytest.mark.parametrize("text", [
    # Ordinary accounting vocabulary. Matching these bare stems suppressed valid
    # findings, and on an Item 4.02/1.05 filing could drop the required critical
    # finding and escalate to a whole-filing CRITICAL_COVERAGE withhold.
    "The Company sold its European distribution unit to a strategic buyer",
    "Average selling prices declined across the memory product line",
    "Cost of goods sold rose because of unfavorable manufacturing variances",
    "New purchase commitments were disclosed for wafer capacity",
    "The Company disposed of its legacy hardware business",
])
def test_descriptive_commercial_verbs_are_not_trade_instructions(text):
    assert contains_trade_instruction(text) is False


@pytest.mark.parametrize("text", [
    "You should sell before the print",
    "Investors should buy now",
    "Shareholders must hold the position",
])
def test_directed_instructions_using_those_verbs_are_still_blocked(text):
    assert contains_trade_instruction(text) is True


@pytest.mark.parametrize("text", [
    # Period nouns, not magnitudes. "quarter"/"half" are unavoidable in non-reliance
    # and guidance headlines; treating them as quantities dropped the finding.
    "Inventory obsolescence charges were recorded in the fourth quarter",
    "The auditor issued a going concern paragraph in the current quarter",
    "Guidance was withdrawn for the second half of the fiscal year",
    # Compound noun, not a fraction — the canonical Item 1.05 cyber headline shape.
    "A third-party service provider suffered a security incident",
])
def test_period_nouns_and_compounds_are_not_quantities(text):
    assert contains_authored_quantity(text) is False


@pytest.mark.parametrize("text", [
    "Revenue increased by a quarter",
    "Roughly a third of revenue is at risk",
    "Revenue fell by half",
])
def test_fraction_phrasing_is_still_a_quantity(text):
    assert contains_authored_quantity(text) is True


def test_canonical_non_reliance_headline_carries_no_violation():
    """The Item 4.02 headline the launch must never drop.

    A missed non-reliance event is disqualifying (AGENTS.md 13). The word "quarter" in
    otherwise unavoidable phrasing used to raise AUTHORED_NUMBER, dropping the required
    critical finding and failing CRITICAL_COVERAGE for the whole filing.
    """
    assert authored_text_violations(
        "The Audit Committee concluded previously issued financial statements for the "
        "prior quarter should no longer be relied upon"
    ) == []


@pytest.mark.parametrize("text", [
    # Bare imperatives and indirect advisory frames. Removing the trade verbs from the
    # lexicon (rather than requiring an advisory frame around them) let every one of
    # these publish to the user-visible brief.
    "Sell the shares ahead of the restatement",
    "Time to sell",
    "Readers should sell",
    "Our recommendation is to sell",
    "Investors should immediately sell",
    "Investors should, given the restatement, sell",
    "Investors are advised to sell",
    "We recommend that investors sell",
    "Shareholders should liquidate",
    "You should short the stock",
    "Buy on weakness",
    "The shares should be sold",
    "We are sellers of the name",
    "Holders must sell",
    "Trim the position",
])
def test_advisory_frames_are_blocked_however_they_are_phrased(text):
    assert contains_trade_instruction(text) is True


@pytest.mark.parametrize("text", [
    "Buying patterns shifted toward direct channels",
    "Trimming the workforce reduced headcount",
    "Selling, general and administrative expenses rose",
    "Purchase commitments increased",
    "Share buyback program was suspended",
    "Management is considering selling the division",
    "Management recommended the board review the sale of the unit",
])
def test_descriptive_prose_using_trade_verbs_stays_clean(text):
    assert contains_trade_instruction(text) is False


@pytest.mark.parametrize(("text", "flagged"), [
    ("Cut in half", True),
    ("Half the workforce was terminated", True),
    ("Nearly half of the loan portfolio was reclassified", True),
    ("Guidance was withdrawn for the second half of the fiscal year", False),
    ("First half results were reaffirmed", False),
    ("Half-year reporting was unchanged", False),
])
def test_half_is_a_magnitude_except_in_period_senses(text, flagged):
    assert contains_authored_quantity(text) is flagged
