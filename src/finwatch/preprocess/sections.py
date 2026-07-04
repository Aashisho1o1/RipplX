"""Canonical section routing for 10-K and 10-Q filings (deterministic).

Strategy: over the flat text, find line-start ``Item N`` / ``Part N`` headers that
are NOT inside anchor links (which excludes the table of contents). Item numbers
alone identify sections in a 10-K; a 10-Q needs Part context because Item 2 means
MD&A in Part I but Unregistered Sales in Part II. Each recognised section spans
from its header to the next header of any kind. A title-keyword check guards each
mapping so a stray body reference cannot masquerade as a section.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass

from finwatch.preprocess.html import NormalizedDoc, normalize_whitespace_line

_ITEM_RE = re.compile(r"(?im)^[ \t]*Item[ \t]+(\d{1,2}[A-Z]?)[.\):\s]")
_PART_RE = re.compile(r"(?im)^[ \t]*Part[ \t]+(IV|III|II|I)\b")
# Leading item token of either form ("Item 7." or "Item 2.02"), for header-shape checks.
_ITEM_TOKEN_RE = re.compile(r"(?i)^Item\s+\d{1,2}[A-Z]?(?:\.\d{2})?[.\):\s]+")
# Item 8 sub-sections (auditor's report + notes), detected within the financials span.
_AUDITOR_RE = re.compile(
    r"(?im)^.{0,25}Report\s+of\s+Independent\s+Registered\s+Public\s+Accounting\s+Firm"
)
_NOTES_RE = re.compile(
    r"(?im)^.{0,25}Notes\s+to\s+(?:the\s+)?(?:Condensed\s+)?(?:Consolidated\s+)?Financial\s+Statements"
)

TENK_MAP: dict[str, str] = {
    "1": "business", "1A": "risk_factors", "3": "legal", "7": "mdna",
    "7A": "market_risk", "8": "financials", "9A": "controls",
}
TENQ_MAP: dict[tuple[str, str], str] = {
    ("I", "1"): "financials", ("I", "2"): "mdna", ("I", "3"): "market_risk",
    ("I", "4"): "controls", ("II", "1"): "legal", ("II", "1A"): "risk_factor_changes",
}
# Required title keywords per section (lowercased substring, any-of).
_TITLE_HINTS: dict[str, tuple[str, ...]] = {
    "business": ("business",),
    "risk_factors": ("risk factor",),
    "risk_factor_changes": ("risk factor",),
    "legal": ("legal proceeding",),
    "mdna": ("management", "discussion"),
    "market_risk": ("market risk", "quantitative and qualitative"),
    "financials": ("financial statement",),
    "controls": ("controls",),
}


@dataclass
class Section:
    section_key: str
    title: str
    char_start: int
    char_end: int
    element_id: str | None
    is_furnished: bool
    text: str
    text_sha256: str


@dataclass
class _Header:
    value: str          # item number ("1A") or part roman ("II")
    offset: int
    title_line: str     # normalized text of the header's line


def _line_at(text: str, start: int) -> str:
    end = text.find("\n", start)
    return text[start : end if end != -1 else len(text)]


def _headers(doc: NormalizedDoc, regex: re.Pattern) -> list[_Header]:
    """Non-link, line-start matches in document order (ToC excluded via is_link)."""
    out: list[_Header] = []
    for m in regex.finditer(doc.text):
        if doc.is_link_at(m.start()):
            continue
        out.append(_Header(m.group(1).upper(), m.start(),
                           normalize_whitespace_line(_line_at(doc.text, m.start()))))
    return out


def _title_ok(key: str, title_line: str) -> bool:
    hints = _TITLE_HINTS.get(key)
    if not hints:
        return True
    low = title_line.lower()
    return any(h in low for h in hints)


def _is_header_shape(title_line: str) -> bool:
    """True when the line looks like a real section header rather than a prose
    cross-reference. Real titles start (right after the item token) with an
    upper-case word (Title-Case or ALL-CAPS); a sentence like "Item 7 contains
    additional Management's Discussion..." starts with a lower-case connective."""
    m = _ITEM_TOKEN_RE.match(title_line)
    after = (title_line[m.end():] if m else "").strip()
    return bool(after) and after[0].isupper()


def _accept(key: str, title_line: str) -> bool:
    return _title_ok(key, title_line) and _is_header_shape(title_line)


def _make_section(doc: NormalizedDoc, key: str, header: _Header, end: int,
                  is_furnished: bool) -> Section:
    text = doc.text[header.offset : end]
    return Section(
        section_key=key,
        title=header.title_line[:200],
        char_start=header.offset,
        char_end=end,
        element_id=doc.element_id_at(header.offset),
        is_furnished=is_furnished,
        text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def dedupe_largest(sections: list[Section]) -> list[Section]:
    """Keep the LARGEST-span section per key, in document order. Real section
    bodies dwarf table-of-contents entries and prose cross-references, so this is
    what defeats a plain-text (non-hyperlinked) ToC where every 'Item N' line is a
    non-link candidate."""
    best: dict[str, Section] = {}
    for s in sections:
        cur = best.get(s.section_key)
        if cur is None or (s.char_end - s.char_start) > (cur.char_end - cur.char_start):
            best[s.section_key] = s
    return sorted(best.values(), key=lambda s: s.char_start)


def split_10k(doc: NormalizedDoc) -> list[Section]:
    items = _headers(doc, _ITEM_RE)
    boundaries = sorted({h.offset for h in items})
    sections: list[Section] = []
    for h in items:
        key = TENK_MAP.get(h.value)
        if key is None or not _accept(key, h.title_line):
            continue
        end = _next_boundary(boundaries, h.offset, len(doc.text))
        sections.append(_make_section(doc, key, h, end, is_furnished=False))
    result = dedupe_largest(sections)
    financials = next((s for s in result if s.section_key == "financials"), None)
    if financials is not None:
        result.extend(_item8_subsections(doc, financials))
    return sorted(result, key=lambda s: s.char_start)


def split_10q(doc: NormalizedDoc) -> list[Section]:
    parts = _headers(doc, _PART_RE)
    items = _headers(doc, _ITEM_RE)
    boundaries = sorted({h.offset for h in items} | {p.offset for p in parts})
    part_offsets = [p.offset for p in parts]
    sections: list[Section] = []
    for h in items:
        idx = bisect.bisect_right(part_offsets, h.offset) - 1
        part = parts[idx].value if idx >= 0 else None
        key = TENQ_MAP.get((part, h.value)) if part else None
        if key is None or not _accept(key, h.title_line):
            continue
        end = _next_boundary(boundaries, h.offset, len(doc.text))
        sections.append(_make_section(doc, key, h, end, is_furnished=False))
    return dedupe_largest(sections)


def _item8_subsections(doc: NormalizedDoc, financials: Section) -> list[Section]:
    """Best-effort auditor_report + notes sub-sections within the Item 8 span.

    Additive pointers alongside the ``financials`` umbrella (they may overlap its
    tail). Ordering of the auditor's report vs. the notes varies by issuer, so each
    sub-section runs from its heading to the next detected sub-heading (or Item 8 end).
    """
    span = doc.text[financials.char_start : financials.char_end]
    marks: list[tuple[int, str]] = []
    for key, rx in (("auditor_report", _AUDITOR_RE), ("notes", _NOTES_RE)):
        m = rx.search(span)
        if m:
            marks.append((financials.char_start + m.start(), key))
    marks.sort()
    out: list[Section] = []
    for i, (off, key) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else financials.char_end
        text = doc.text[off:end]
        out.append(Section(
            section_key=key, title=normalize_whitespace_line(_line_at(doc.text, off))[:200],
            char_start=off, char_end=end, element_id=doc.element_id_at(off),
            is_furnished=False, text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        ))
    return out


def _next_boundary(boundaries: list[int], offset: int, fallback: int) -> int:
    i = bisect.bisect_right(boundaries, offset)
    return boundaries[i] if i < len(boundaries) else fallback
