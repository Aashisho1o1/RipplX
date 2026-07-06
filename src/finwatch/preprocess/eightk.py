"""8-K item splitter + furnished detection (deterministic).

8-K items are ``Item <major>.<minor>`` (e.g. Item 2.02). Each present item becomes
a ``item_<major>_<minor>`` section. Items 2.02 (results of operations) and 7.01
(Reg FD) are *furnishable*: they are marked ``is_furnished`` when the filing carries
the standard furnishing clause ("furnished", or "shall not be deemed 'filed'").
"""
from __future__ import annotations

import hashlib
import re

from finwatch.preprocess.html import NormalizedDoc, normalize_whitespace_line
from finwatch.preprocess.sections import (
    Section,
    _is_header_shape,
    _next_boundary,
    dedupe_largest,
)

_EIGHTK_ITEM_RE = re.compile(r"(?im)^[ \t]*Item[ \t]+(\d)\.(\d{2})[.\):\s]")
_ITEM_ONLY_RE = re.compile(r"(?i)^Item\s+\d\.\d{2}[.\):]?")
_FURNISH_RE = re.compile(
    r"(furnished\b"
    r"|shall\s+not\s+be\s+deemed\s+.{0,20}?filed"
    r"|not\s+be\s+deemed\s+to\s+be\s+filed)",
    re.IGNORECASE | re.DOTALL,
)
_FURNISHABLE = frozenset({("2", "02"), ("7", "01")})


def furnishing_present(text: str) -> bool:
    return bool(_FURNISH_RE.search(text))


def _header_title(text: str, start: int) -> str | None:
    """Return a validated 8-K header, including SEC's common split-line shape.

    Many filings render ``Item 5.02.`` in one block and its title in the next.
    The shared header guard intentionally rejects an item token with no title, so
    join only the immediate next non-empty, title-cased line before applying it.
    """
    line_end = text.find("\n", start)
    line_end = line_end if line_end != -1 else len(text)
    item_line = normalize_whitespace_line(text[start:line_end])
    if _is_header_shape(item_line):
        return item_line
    if not _ITEM_ONLY_RE.fullmatch(item_line):
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


def split_8k(doc: NormalizedDoc) -> list[Section]:
    headers: list[tuple[str, str, int, str]] = []
    for m in _EIGHTK_ITEM_RE.finditer(doc.text):
        if doc.is_link_at(m.start()):
            continue
        title = _header_title(doc.text, m.start())
        if title is None:  # reject prose "Item 2.02 is discussed above"
            continue
        headers.append((m.group(1), m.group(2), m.start(), title))
    boundaries = sorted({off for _, _, off, _ in headers})

    sections: list[Section] = []
    for major, minor, off, title in headers:
        end = _next_boundary(boundaries, off, len(doc.text))
        text = doc.text[off:end]
        # Furnished only if this item is furnishable AND its OWN span carries the
        # furnishing legend, so a filed 2.02 beside a furnished 7.01 is not swept in.
        is_furnished = (major, minor) in _FURNISHABLE and furnishing_present(text)
        sections.append(Section(
            section_key=f"item_{major}_{minor}",
            title=title[:200],
            char_start=off,
            char_end=end,
            element_id=doc.element_id_at(off),
            is_furnished=is_furnished,
            text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        ))
    return dedupe_largest(sections)
