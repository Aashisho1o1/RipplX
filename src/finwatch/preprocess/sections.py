"""Canonical section routing for 10-K and 10-Q filings (deterministic).

Strategy: over the flat text, find line-start ``Item N`` / ``Part N`` headers that
identify canonical sections, following same-document table-of-contents links when
available. Item numbers alone identify sections in a 10-K; a 10-Q uses Part context
plus its canonical title because Item 2 means MD&A in Part I but Unregistered Sales
in Part II. A narrow fallback recovers substantive MD&A/financial statements placed
later in the same primary 10-K after short incorporation stubs.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass

from finwatch.preprocess.html import NormalizedDoc, normalize_whitespace_line

_ITEM_RE = re.compile(r"(?im)^[ \t]*Item[ \t]+(\d{1,2}[A-Z]?)[.\):\s\-–—•]")
_PART_RE = re.compile(r"(?im)^[ \t]*Part[ \t]+(IV|III|II|I)\b")
# Leading item token of either form ("Item 7." or "Item 2.02"), for header-shape checks.
_ITEM_TOKEN_RE = re.compile(
    r"(?i)^Item\s+\d{1,2}[A-Z]?(?:\.\d{2})?[.\):\s\-–—•]+"
)
# A separator run (dash / en- or em-dash / bullet / colon / space) that some filers
# place between the item token and its title, e.g. "Item 5.02 - Departure of...".
_SEP_RUN_RE = re.compile(r"^[\s\-–—•:]+")
# A header line that is ONLY the item token ("Item 7." / "Item 1A."), whose title
# renders on the following line (SEC's common two-cell table layout after flattening).
_TENKQ_ITEM_ONLY_RE = re.compile(r"(?i)^Item\s+\d{1,2}[A-Z]?[.\):\-–—•]?")
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
_STRUCTURAL_MIN_CHARS = {
    "business": 1_000,
    "risk_factors": 1_000,
    "mdna": 1_000,
    "financials": 3_000,
}
_ALT_MDNA_RE = re.compile(
    r"(?im)^[ \t]*management[’']s\s+discussion\s+and\s+analysis"
    r"(?:\s+of\s+financial\s+condition\s+and\s+results\s+of\s+operations)?"
    r"[.:]?[ \t]*$"
)
_ALT_FINANCIALS_RES = (
    re.compile(
        r"(?im)^[ \t]*(?:audited\s+)?(?:consolidated\s+)?financial\s+statements"
        r"(?:\s+and\s+supplementary\s+data)?[.:]?[ \t]*$"
    ),
    re.compile(
        r"(?im)^[ \t]*report\s+of\s+independent\s+registered\s+public\s+"
        r"accounting\s+firm[.:]?[ \t]*$"
    ),
)


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
    source_offset: int | None = None  # ToC position before following an internal link


def _line_at(text: str, start: int) -> str:
    end = text.find("\n", start)
    return text[start : end if end != -1 else len(text)]


def _headers(doc: NormalizedDoc, regex: re.Pattern,
             item_only_re: re.Pattern | None = None) -> list[_Header]:
    """Non-link, line-start matches in document order (ToC excluded via is_link).

    When ``item_only_re`` is given, a bare item-token header ("Item 7." alone on a
    line) adopts the following title line via :func:`_resolve_header_title`, so the
    two-cell 'Item 7.' | 'Management's Discussion...' table layout is not dropped.
    The header offset is unchanged, so section boundaries/hashes are unaffected.
    """
    out: list[_Header] = []
    for m in regex.finditer(doc.text):
        single = normalize_whitespace_line(_line_at(doc.text, m.start()))
        if item_only_re is not None:
            title_line = _resolve_header_title(doc.text, m.start(), item_only_re) or single
        else:
            title_line = single
        source_end = _header_source_end(doc.text, m.start(), item_only_re)
        linked_target = doc.link_target_between(m.start(), source_end)
        if linked_target is not None and linked_target > m.start():
            offset = linked_target
        elif doc.is_link_at(m.start()) and linked_target is None:
            continue
        else:
            # A linked body heading may point backward to the table of contents.
            # Keep the real heading at its current location rather than following back.
            offset = m.start()
        out.append(_Header(m.group(1).upper(), offset, title_line, m.start()))
    return sorted(out, key=lambda header: header.offset)


def _title_ok(key: str, title_line: str) -> bool:
    hints = _TITLE_HINTS.get(key)
    if not hints:
        return True
    low = title_line.lower()
    return any(h in low for h in hints)


def _is_header_shape(title_line: str) -> bool:
    """True when the line looks like a real section header rather than a prose
    cross-reference. Real titles start (right after the item token, past any
    separator run) with an upper-case word (Title-Case or ALL-CAPS); a sentence
    like "Item 7 contains additional Management's Discussion..." starts with a
    lower-case connective. A leading separator ("Item 5.02 - Departure...") is
    skipped so a dash/colon/dash-em between token and title does not drop the item."""
    m = _ITEM_TOKEN_RE.match(title_line)
    if not m:
        return False
    after = _SEP_RUN_RE.sub("", title_line[m.end():])
    return bool(after) and after[0].isupper()


def _resolve_header_title(text: str, start: int, item_only_re: re.Pattern) -> str | None:
    """Resolve a header's title, joining SEC's split-line shape where the item
    token ("Item 7.") renders on its own line and the title follows on the next.

    Returns the (possibly joined) title line, or ``None`` when the line is a prose
    cross-reference or a bare item token with no following title. Callers always
    take offsets/hashes from the item-token offset, so joining the title text never
    moves a section boundary. Shared by the 10-K/10-Q and 8-K splitters.
    """
    line_end = text.find("\n", start)
    line_end = line_end if line_end != -1 else len(text)
    item_line = normalize_whitespace_line(text[start:line_end])
    if _is_header_shape(item_line):
        return item_line
    if not item_only_re.fullmatch(item_line):
        return None
    cursor = line_end + 1
    for _ in range(3):
        next_end = text.find("\n", cursor)
        next_end = next_end if next_end != -1 else len(text)
        candidate = normalize_whitespace_line(text[cursor:next_end])
        if candidate:
            joined = f"{item_line} {candidate}"
            return joined if _is_header_shape(joined) else None
        if next_end == len(text):
            break
        cursor = next_end + 1
    return None


def _header_source_end(
    text: str,
    start: int,
    item_only_re: re.Pattern | None,
) -> int:
    """End of the source header row, including a split-line linked title."""
    line_end = text.find("\n", start)
    line_end = line_end if line_end != -1 else len(text)
    if item_only_re is None:
        return line_end
    item_line = normalize_whitespace_line(text[start:line_end])
    if not item_only_re.fullmatch(item_line):
        return line_end
    cursor = line_end + 1
    for _ in range(3):
        next_end = text.find("\n", cursor)
        next_end = next_end if next_end != -1 else len(text)
        if normalize_whitespace_line(text[cursor:next_end]):
            return next_end
        if next_end == len(text):
            break
        cursor = next_end + 1
    return line_end


def _accept_candidate(key: str, title_line: str, span_length: int) -> bool:
    """Accept standard titles, or a substantial structurally bounded Item span.

    Some issuers use a company/page heading after the Item token instead of the
    Regulation S-K title. The Item sequence still supplies a reliable boundary;
    requiring a meaningful span prevents a table-of-contents stub from winning.
    """
    if not _is_header_shape(title_line):
        return False
    structural_minimum = _STRUCTURAL_MIN_CHARS.get(key)
    return _title_ok(key, title_line) or (
        structural_minimum is not None and span_length >= structural_minimum
    )


def _tenq_key(part: str | None, item: str, title_line: str) -> str | None:
    """Route a 10-Q item using Part context, corrected by its canonical title.

    Some financial-company filings place the substantive MD&A before a repeated
    ``Part I`` body marker, after a plain-text table of contents has already emitted
    ``Part II``. In that shape physical Part context is stale. Item number plus the
    standard SEC title is unambiguous for every section we retain, so use it to
    correct (never invent) the mapping. A Part II ``Item 2. Unregistered Sales``
    cannot pass the MD&A title check and remains excluded.
    """
    contextual = TENQ_MAP.get((part, item)) if part else None
    if contextual is not None and _title_ok(contextual, title_line):
        return contextual
    titled = {
        key
        for (candidate_part, candidate_item), key in TENQ_MAP.items()
        if candidate_item == item
        and candidate_part in {"I", "II"}
        and _title_ok(key, title_line)
    }
    return next(iter(titled)) if len(titled) == 1 else contextual


_PART_TOKEN_RE = re.compile(r"(?i)^Part\s+(IV|III|II|I)\b[.\):\s]*")


def _is_boundary_header(title_line: str) -> bool:
    """True only for a resolved structural Item heading.

    ``_headers`` has already joined a split-line token to its nearby title when that
    title exists. An unresolved bare token is commonly a running page label (for
    example Microsoft's repeated ``Item 7``) and must not truncate the section.
    """
    return _is_header_shape(title_line)


def _is_part_header_shape(title_line: str) -> bool:
    """True when a ``Part`` match is a real Part header ("Part I", "PART II — OTHER
    INFORMATION") rather than a prose sentence ("Part II of this report discusses...").
    A real header is only the Part token, or the token followed by a Title-Case/ALL-CAPS
    title; a lower-case continuation marks prose. Unvalidated Part markers must not act
    as boundaries or reassign an item to the wrong Part (M4)."""
    m = _PART_TOKEN_RE.match(title_line)
    if not m:
        return False
    after = _SEP_RUN_RE.sub("", title_line[m.end():])
    return after == "" or after[0].isupper()


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
    items = _headers(doc, _ITEM_RE, _TENKQ_ITEM_ONLY_RE)
    # Only validated structural headers may bound a section, so a prose "Item N ..."
    # cross-reference inside a body cannot truncate that body (M4).
    boundaries = sorted({h.offset for h in items if _is_boundary_header(h.title_line)})
    sections: list[Section] = []
    for h in items:
        key = TENK_MAP.get(h.value)
        if key is None:
            continue
        end = _next_boundary(boundaries, h.offset, len(doc.text))
        if not _accept_candidate(key, h.title_line, end - h.offset):
            continue
        sections.append(_make_section(doc, key, h, end, is_furnished=False))
    result = _repair_incorporated_annual_sections(doc, dedupe_largest(sections))
    financials = next((s for s in result if s.section_key == "financials"), None)
    if financials is not None:
        result.extend(_item8_subsections(doc, financials))
    return sorted(result, key=lambda s: s.char_start)


def _alternate_heading(
    doc: NormalizedDoc,
    patterns: tuple[re.Pattern, ...],
    after: int,
    minimum_span: int,
) -> _Header | None:
    """Find a canonical standalone heading later in the same primary document.

    Forward same-document links are strongest: they relocate an annual-report table
    of contents entry to its body anchor. If no such link exists, an exact standalone
    heading is accepted. Sentence fragments and arbitrary keyword hits cannot enter
    because every pattern consumes the complete normalized line.
    """
    linked: list[_Header] = []
    plain: list[_Header] = []
    for pattern in patterns:
        for match in pattern.finditer(doc.text):
            line_end = doc.text.find("\n", match.start())
            line_end = line_end if line_end != -1 else len(doc.text)
            target = doc.link_target_between(match.start(), line_end)
            followed_link = target is not None and target > match.start()
            offset = target if followed_link else match.start()
            if offset <= after or len(doc.text) - offset < minimum_span:
                continue
            header = _Header(
                value="",
                offset=offset,
                title_line=normalize_whitespace_line(match.group(0)),
                source_offset=match.start(),
            )
            (linked if followed_link else plain).append(header)
    candidates = linked or plain
    return min(candidates, key=lambda header: header.offset) if candidates else None


def _replacement_section(
    doc: NormalizedDoc,
    key: str,
    header: _Header,
    end: int,
) -> Section | None:
    if end - header.offset < _STRUCTURAL_MIN_CHARS[key]:
        return None
    return _make_section(doc, key, header, end, is_furnished=False)


def _repair_incorporated_annual_sections(
    doc: NormalizedDoc,
    sections: list[Section],
) -> list[Section]:
    """Replace only suspiciously short Item 7/8 incorporation stubs.

    Large issuers sometimes append a shareholder-report/financial section inside
    the same primary 10-K while the formal Item 7 and Item 8 bodies merely point to
    it. This is not a general keyword fallback: it activates only for an already
    identified but undersized canonical section and only accepts exact standalone
    annual-report headings with a substantial body.
    """
    by_key = {section.section_key: section for section in sections}
    mdna = by_key.get("mdna")
    financials = by_key.get("financials")
    needs_mdna = mdna is not None and len(mdna.text) < _STRUCTURAL_MIN_CHARS["mdna"]
    needs_financials = (
        financials is not None
        and len(financials.text) < _STRUCTURAL_MIN_CHARS["financials"]
    )
    if not needs_mdna and not needs_financials:
        return sections

    financial_header = None
    if needs_financials and financials is not None:
        financial_header = _alternate_heading(
            doc,
            _ALT_FINANCIALS_RES,
            financials.char_end,
            _STRUCTURAL_MIN_CHARS["financials"],
        )

    if needs_mdna and mdna is not None:
        mdna_header = _alternate_heading(
            doc,
            (_ALT_MDNA_RE,),
            mdna.char_end,
            _STRUCTURAL_MIN_CHARS["mdna"],
        )
        if mdna_header is not None:
            mdna_end = (
                financial_header.offset
                if financial_header is not None and financial_header.offset > mdna_header.offset
                else len(doc.text)
            )
            replacement = _replacement_section(doc, "mdna", mdna_header, mdna_end)
            if replacement is not None:
                by_key["mdna"] = replacement

    if financial_header is not None:
        replacement = _replacement_section(
            doc, "financials", financial_header, len(doc.text)
        )
        if replacement is not None:
            by_key["financials"] = replacement

    return sorted(by_key.values(), key=lambda section: section.char_start)


def split_10q(doc: NormalizedDoc) -> list[Section]:
    # Only validated Part headers may act as boundaries or reassign an item's Part;
    # a prose "Part II ..." reference must not move Item 2 out of Part I (M4).
    parts = [p for p in _headers(doc, _PART_RE) if _is_part_header_shape(p.title_line)]
    items = _headers(doc, _ITEM_RE, _TENKQ_ITEM_ONLY_RE)
    item_bounds = {h.offset for h in items if _is_boundary_header(h.title_line)}
    boundaries = sorted(item_bounds | {p.offset for p in parts})
    source_parts = sorted(
        parts,
        key=lambda part: (
            part.source_offset if part.source_offset is not None else part.offset
        ),
    )
    part_offsets = [
        part.source_offset if part.source_offset is not None else part.offset
        for part in source_parts
    ]
    target_parts = sorted(parts, key=lambda part: part.offset)
    target_part_offsets = [part.offset for part in target_parts]
    sections: list[Section] = []
    for h in items:
        followed_link = h.source_offset is not None and h.source_offset != h.offset
        if followed_link:
            lookup_offset = h.source_offset
            lookup_parts = source_parts
            lookup_offsets = part_offsets
        else:
            lookup_offset = h.offset
            lookup_parts = target_parts
            lookup_offsets = target_part_offsets
        idx = bisect.bisect_right(lookup_offsets, lookup_offset) - 1
        part = lookup_parts[idx].value if idx >= 0 else None
        key = _tenq_key(part, h.value, h.title_line)
        if key is None:
            continue
        end = _next_boundary(boundaries, h.offset, len(doc.text))
        if not _accept_candidate(key, h.title_line, end - h.offset):
            continue
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
