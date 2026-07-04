"""Paragraph-level risk-factor diff (deterministic).

Compares the current filing's risk-factor section against the prior comparable
filing's, producing added / removed / modified paragraph lists with offsets so P1
analyzes a diff rather than two whole documents. Paragraph = a normalized
non-trivial block (the normalizer already breaks blocks with newlines).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from finwatch.preprocess.html import normalize_whitespace_line

_MODIFIED_THRESHOLD = 0.6  # SequenceMatcher ratio to call a replace "modified" vs add+remove


@dataclass
class Paragraph:
    text: str          # normalized (whitespace-collapsed) paragraph text
    char_start: int    # offset within its own section text
    char_end: int


@dataclass
class ModifiedPair:
    prior: Paragraph
    current: Paragraph
    similarity: float


@dataclass
class RiskFactorDiff:
    added: list[Paragraph] = field(default_factory=list)
    removed: list[Paragraph] = field(default_factory=list)
    modified: list[ModifiedPair] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)


def _substantive(norm: str) -> bool:
    return len(norm) >= 3 and any(ch.isalpha() for ch in norm)


def split_paragraphs(text: str) -> list[Paragraph]:
    """Split section text into normalized, substantive paragraphs with offsets."""
    out: list[Paragraph] = []
    pos = 0
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        pos = end + 1  # account for the split newline
        norm = normalize_whitespace_line(line)
        if _substantive(norm):
            out.append(Paragraph(text=norm, char_start=start, char_end=end))
    return out


def diff_risk_factors(prior_text: str, current_text: str) -> RiskFactorDiff:
    prior = split_paragraphs(prior_text)
    current = split_paragraphs(current_text)
    diff = RiskFactorDiff()
    matcher = difflib.SequenceMatcher(
        None, [p.text for p in prior], [c.text for c in current], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            diff.removed.extend(prior[i1:i2])
        elif tag == "insert":
            diff.added.extend(current[j1:j2])
        elif tag == "replace":
            _resolve_replace(diff, prior[i1:i2], current[j1:j2])
    return diff


def _resolve_replace(diff: RiskFactorDiff, prior: list[Paragraph],
                     current: list[Paragraph]) -> None:
    """Align a replace block by SIMILARITY, not position.

    Positional pairing mispairs whenever a paragraph is inserted or removed inside a
    reworded run (everything after shifts by one). Instead, greedily match the
    highest-similarity prior↔current pairs (each used once); unmatched prior ⇒
    removed, unmatched current ⇒ added. Deterministic: ties break on index.
    """
    candidates: list[tuple[float, int, int]] = []
    for i, p in enumerate(prior):
        for j, c in enumerate(current):
            ratio = difflib.SequenceMatcher(None, p.text, c.text).ratio()
            if ratio >= _MODIFIED_THRESHOLD:
                candidates.append((ratio, i, j))
    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))

    used_prior: set[int] = set()
    used_current: set[int] = set()
    matched: list[tuple[int, int, float]] = []
    for ratio, i, j in candidates:
        if i in used_prior or j in used_current:
            continue
        used_prior.add(i)
        used_current.add(j)
        matched.append((i, j, ratio))

    for i, j, ratio in sorted(matched, key=lambda t: t[1]):
        diff.modified.append(
            ModifiedPair(prior=prior[i], current=current[j], similarity=round(ratio, 3))
        )
    diff.removed.extend(p for i, p in enumerate(prior) if i not in used_prior)
    diff.added.extend(c for j, c in enumerate(current) if j not in used_current)
