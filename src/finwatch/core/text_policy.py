"""Shared fail-closed policy for LLM-authored launch text.

Exact SEC quotations may contain quantities and trading language because they are
source material. These checks apply only to prose authored by the model (currently
finding headlines), where quantities and recommendations are forbidden.
"""

from __future__ import annotations

import re

_AUTHORED_QUANTITY = re.compile(
    r"\d|"
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"billion|trillion|percent|percentage|double|doubled|triple|tripled|"
    r"quadruple|quadrupled|quintuple|quintupled|half|halved|twice|thrice|"
    r"twofold|threefold|fourfold|fivefold|sixfold|sevenfold|eightfold|ninefold|"
    r"tenfold|dozens?|scores?|quarters?|fractions?|fractional)\b|"
    # Fraction phrasing ("a third", "a tenth"). A bare ordinal is deliberately not
    # matched so rank prose ("its third-largest customer") is not read as a quantity.
    r"\ba\s+(?:third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b|"
    r"\bbasis\s+points?\b",
    re.IGNORECASE,
)

# Existing launch policy intentionally excludes standalone trade-action vocabulary
# from authored headlines. Inflected forms (buying/bought/selling/sold, ...) and
# positive-recommendation phrasing ("recommend buying", "consider adding") close
# the gaps the original verb-stem-only list left open.
_TRADE_ACTION_WORD = re.compile(
    r"\b(?:buy|buys|buying|bought|sell|sells|selling|sold|hold|"
    r"trim|trims|trimming|accumulate|accumulates|accumulating|"
    r"purchase|purchases|purchasing|dispose|disposes|disposing)\b",
    re.IGNORECASE,
)
_TRADE_ADVICE_PHRASE = re.compile(
    r"(?:"
    r"\bstay\s+away(?:\s+from)?\b|"
    r"\breduce\s+(?:(?:your|the)\s+)?exposure\b|"
    r"\bexit\s+(?:(?:your|the)\s+)?(?:position|holding|stake)\b|"
    r"\bavoid\s+(?:the\s+)?(?:stock|shares?|security|company)\b|"
    r"\b(?:you|investors?|shareholders?)\s+(?:should|must|need\s+to)\s+"
    r"(?:avoid|exit|reduce|stay\s+away|consider|add|adding|accumulate|accumulating)\b|"
    # A recommendation to take a *trade* action (not generic "recommended restating").
    r"\brecommend(?:s|ed|ing)?\s+"
    r"(?:buying|adding|accumulating|purchasing|selling|exiting|avoiding|reducing|"
    r"staying\s+away|trimming|holding)\b|"
    r"\bconsider\s+(?:buying|adding|accumulating|purchasing|selling|trimming|exiting)\b"
    r")",
    re.IGNORECASE,
)


def contains_authored_quantity(text: str) -> bool:
    """Whether model-authored prose contains a numeric quantity or expression."""
    return bool(_AUTHORED_QUANTITY.search(text))


def contains_trade_instruction(text: str) -> bool:
    """Whether model-authored prose contains forbidden trade-action language."""
    return bool(_TRADE_ACTION_WORD.search(text) or _TRADE_ADVICE_PHRASE.search(text))
