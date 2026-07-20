"""Shared fail-closed policy for LLM-authored launch text.

Exact SEC quotations may contain quantities and trading language because they are
source material. These checks apply only to prose authored by the model (currently
finding headlines), where quantities and recommendations are forbidden.
"""

from __future__ import annotations

import re

from finwatch.core.types import FORBIDDEN_VOCABULARY

_AUTHORED_QUANTITY = re.compile(
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
    r"\b(?:basis\s+points?|bps)\b|"
    r"\b(?:low|mid|high)[\s-]+(?:single[\s-]+digits?|teens?|tens?)\b",
    re.IGNORECASE,
)

# Existing launch policy intentionally excludes standalone trade-action vocabulary
# from authored headlines. Inflected forms (buying/bought/selling/sold, ...) and
# positive-recommendation phrasing ("recommend buying", "consider adding") close
# the gaps the original verb-stem-only list left open.

#AS: COmment over above comment and codes below and other parts: Will hardcoding
# these avoiding words wiork: LLM communication can give tehse words in repsonse,
# and just bvcuase we avoid them here, may just jeopoardiize the LLm response...
# think about the better approach.

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

_PRICE_TARGET = re.compile(
    r"(?:"
    r"\bprice\s+target\b|"
    r"\btarget\s+price\b|"
    r"\bwill\s+(?:reach|hit)\b|"
    r"\$\d+(?:\.\d+)?\s*(?:PT\b|target\b|price\s+target\b)"
    r")",
    re.IGNORECASE,
)
_FIRST_PERSON_VALUATION = re.compile(
    r"\b(?:(?:we|i)\s+(?:assign|estimate|believe|calculate)|(?:our|my))\s+"
    r"(?:a\s+)?(?:fair|intrinsic)\s+value\b",
    re.IGNORECASE,
)
_FORBIDDEN_PATTERNS = tuple(
    re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
    for term in FORBIDDEN_VOCABULARY
)

_VIOLATION_ORDER = (
    "quantity",
    "trade_instruction",
    "price_target",
    "first_person_valuation",
    "forbidden_vocabulary",
)


def contains_authored_quantity(text: str) -> bool:
    """Whether model-authored prose contains a numeric quantity or expression."""
    return any(char.isnumeric() for char in text) or bool(_AUTHORED_QUANTITY.search(text))


def contains_trade_instruction(text: str) -> bool:
    """Whether model-authored prose contains forbidden trade-action language."""
    return bool(_TRADE_ACTION_WORD.search(text) or _TRADE_ADVICE_PHRASE.search(text))


def authored_text_violations(text: str) -> list[str]:
    """Return authored-text policy violations in one stable enforcement order.

    Exact SEC quotations are outside this policy. Callers pass only model-authored
    prose, currently finding headlines.
    """
    matched = {
        "quantity": contains_authored_quantity(text),
        "trade_instruction": contains_trade_instruction(text),
        "price_target": bool(_PRICE_TARGET.search(text)),
        "first_person_valuation": bool(_FIRST_PERSON_VALUATION.search(text)),
        "forbidden_vocabulary": any(
            pattern.search(text) for pattern in _FORBIDDEN_PATTERNS
        ),
    }
    return [name for name in _VIOLATION_ORDER if matched[name]]
