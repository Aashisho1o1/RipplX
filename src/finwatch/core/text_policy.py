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
    r"quadruple|quadrupled|quintuple|quintupled|halved|twice|thrice|"
    r"twofold|threefold|fourfold|fivefold|sixfold|sevenfold|eightfold|ninefold|"
    r"tenfold|dozens?|scores?|fractions?|fractional)\b|"
    # Fraction phrasing ("a third", "a quarter", "a tenth"). A bare ordinal is
    # deliberately not matched so rank prose ("its third-largest customer") is not read
    # as a quantity, and a following hyphen is excluded so compound nouns ("a
    # third-party provider" — the canonical Item 1.05 cyber headline) are not either.
    # Matching the fraction phrasing rather than the bare noun is what separates the
    # magnitude "increased by a quarter" from the period "in the fourth quarter"; the
    # latter is unavoidable in Item 4.02 non-reliance headlines.
    r"\ba\s+(?:quarter|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b(?!-)|"
    # "half" is a magnitude ("cut in half", "half the workforce") except in the
    # period senses a filing headline cannot avoid ("second half of the year") and
    # the hyphenated compounds ("half-year", "half-life").
    r"(?<!first\s)(?<!second\s)(?<!latter\s)(?<!fiscal\s)\bhalf\b(?!-)|"
    r"\b(?:basis\s+points?|bps)\b|"
    r"\b(?:low|mid|high)[\s-]+(?:single[\s-]+digits?|teens?|tens?)\b",
    re.IGNORECASE,
)

# Authored headlines may not instruct a trade.
#
# Detection keys on the advisory FRAME, never on a bare verb. Trade verbs are also
# ordinary accounting vocabulary — "the Company sold its European unit", "average
# selling prices declined", "cost of goods sold rose", "new purchase commitments",
# "trimming the workforce", "buying patterns shifted" — so matching them bare
# suppressed valid findings and, on an Item 4.02/1.05 filing, could drop the required
# critical finding and withhold the entire filing.
#
# Deleting those verbs instead is the opposite error and is worse: it let bare
# imperatives ("Sell the shares ahead of the restatement", "Time to sell") publish.
# What separates an instruction from a description is the grammar around the verb —
# an imperative aimed at a security, an advice lead near a trade verb, a passive
# instruction, or a first-person market posture — so that is what is matched.
_SECURITY_OBJECT = (
    r"(?:the\s+|these\s+|those\s+|your\s+|all\s+|more\s+)?"
    r"(?:shares?|stock|stocks|equity|securities|position|holdings?|stake|name)"
)
_IMPERATIVE_VERB = (
    r"(?:sell|buy|hold|trim|accumulate|divest|liquidate|short|exit|avoid|"
    r"offload|unload|dump)"
)
_GERUND_VERB = r"(?:buying|selling|holding|trimming|accumulating|divesting|shorting)"
_TRADE_VERB = (
    r"(?:buy|buys|buying|sell|sells|selling|hold|holds|holding|trim|trims|trimming|"
    r"accumulate|accumulates|accumulating|purchase|purchases|purchasing|"
    r"dispose|disposes|disposing|divest|divests|divesting|"
    r"liquidate|liquidates|liquidating|short|shorts|shorting|"
    r"offload|unload|dump|exit|exits|exiting|avoid|avoids|avoiding|"
    r"add|adds|adding|reduce|reduces|reducing|take\s+profits)"
)
_ADVICE_LEAD = (
    r"(?:should|must|ought\s+to|needs?\s+to|advised\s+to|advises?\s+to|"
    r"urged?\s+to|wise\s+to|time\s+to|recommend(?:s|ed|ing)?|recommendation)"
)
_TRADE_ADVICE_PHRASE = re.compile(
    r"(?:"
    # Headline-initial imperative aimed at a security ("Sell the shares", "Trim the
    # position"). Requiring the object keeps "Purchase commitments increased" and
    # "Selling prices declined" — both legitimate headline openings — clean.
    r"^\s*" + _IMPERATIVE_VERB + r"(?:\s+\w+){0,2}\s+" + _SECURITY_OBJECT + r"\b|"
    r"^\s*(?:sell|buy|short|exit)\s+(?:on|into|before|ahead|now|at)\b|"
    # Gerund-initial aimed at a security ("Buying shares looks attractive"), while
    # "Buying patterns shifted" and "Trimming the workforce" stay clean.
    r"^\s*" + _GERUND_VERB + r"(?:\s+\w+){0,1}\s+" + _SECURITY_OBJECT + r"\b|"
    # An advice lead within a few words of a trade verb. The bounded gap defeats the
    # one-intervening-word bypass ("Investors should immediately sell") and the subject
    # is deliberately unenumerated, so "readers"/"holders"/"one" cannot evade it.
    + _ADVICE_LEAD + r"(?:[\s,;:—-]+\w+){0,4}[\s,;:—-]+" + _TRADE_VERB + r"\b|"
    # Passive instruction and first-person market posture.
    r"\bbe\s+(?:sold|bought|exited|liquidated|divested|offloaded|trimmed)\b|"
    r"\bwe\s+(?:are|would\s+be)\s+(?:buyers|sellers)\b|"
    # Directed forms retained verbatim from the launch policy.
    r"\bstay\s+away(?:\s+from)?\b|"
    r"\breduce\s+(?:(?:your|the)\s+)?exposure\b|"
    r"\bexit\s+(?:(?:your|the)\s+)?(?:position|holding|stake)\b|"
    r"\bavoid\s+(?:the\s+)?(?:stock|shares?|security|company)\b|"
    # "consider" needs a trailing space so the descriptive "is considering a new
    # facility" cannot match.
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
    return bool(_TRADE_ADVICE_PHRASE.search(text))


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
