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
    r"billion|trillion|percent|percentage|double|doubled|triple|tripled|half|"
    r"halved|twofold|threefold|dozens?|scores?|quarters?|fractions?|fractional)\b|"
    r"\bbasis\s+points?\b",
    re.IGNORECASE,
)

# Existing launch policy intentionally excludes standalone trade-action vocabulary
# from authored headlines. The additional phrases cover common recommendations
# that do not use those original verbs ("avoid", "exit", "reduce exposure").
_TRADE_ACTION_WORD = re.compile(
    r"\b(?:buy|sell|hold|trim|accumulate|purchase|dispose)\b",
    re.IGNORECASE,
)
_TRADE_ADVICE_PHRASE = re.compile(
    r"(?:"
    r"\bstay\s+away(?:\s+from)?\b|"
    r"\breduce\s+(?:(?:your|the)\s+)?exposure\b|"
    r"\bexit\s+(?:(?:your|the)\s+)?(?:position|holding|stake)\b|"
    r"\bavoid\s+(?:the\s+)?(?:stock|shares?|security|company)\b|"
    r"\b(?:you|investors?|shareholders?)\s+(?:should|must|need\s+to)\s+"
    r"(?:avoid|exit|reduce|stay\s+away)\b|"
    r"\bwe\s+recommend\s+(?:avoiding|exiting|reducing|staying\s+away)\b"
    r")",
    re.IGNORECASE,
)


def contains_authored_quantity(text: str) -> bool:
    """Whether model-authored prose contains a numeric quantity or expression."""
    return bool(_AUTHORED_QUANTITY.search(text))


def contains_trade_instruction(text: str) -> bool:
    """Whether model-authored prose contains forbidden trade-action language."""
    return bool(_TRADE_ACTION_WORD.search(text) or _TRADE_ADVICE_PHRASE.search(text))
