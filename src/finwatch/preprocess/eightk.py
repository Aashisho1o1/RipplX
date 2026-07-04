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
    _line_at,
    _next_boundary,
    dedupe_largest,
)

_EIGHTK_ITEM_RE = re.compile(r"(?im)^[ \t]*Item[ \t]+(\d)\.(\d{2})[.\):\s]")
_FURNISH_RE = re.compile(
    r"(furnished\b"
    r"|shall\s+not\s+be\s+deemed\s+.{0,20}?filed"
    r"|not\s+be\s+deemed\s+to\s+be\s+filed)",
    re.IGNORECASE | re.DOTALL,
)
_FURNISHABLE = frozenset({("2", "02"), ("7", "01")})


def furnishing_present(text: str) -> bool:
    return bool(_FURNISH_RE.search(text))


def split_8k(doc: NormalizedDoc) -> list[Section]:
    headers: list[tuple[str, str, int, str]] = []
    for m in _EIGHTK_ITEM_RE.finditer(doc.text):
        if doc.is_link_at(m.start()):
            continue
        title = normalize_whitespace_line(_line_at(doc.text, m.start()))
        if not _is_header_shape(title):  # reject prose "Item 2.02 is discussed above"
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
