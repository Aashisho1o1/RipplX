"""HTML → normalized text with per-run provenance (element id + link flag).

The section router needs (a) a flat text it can regex over, (b) for any character
offset, the nearest HTML element id (for provenance) and whether that text sits
inside an anchor link. The link flag is what lets us skip the table-of-contents:
in modern SEC filings ToC entries are ``<a href="#...">Item 2.</a>`` links, while
real section headers are non-link bold text.

Deterministic, stdlib + selectolax only. No I/O.
"""
from __future__ import annotations

import unicodedata
from urllib.parse import unquote

from selectolax.parser import HTMLParser

# Tags whose boundaries imply a line break in the flat text.
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "hr", "tr", "table", "thead", "tbody", "td", "th",
    "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
    "header", "footer", "blockquote", "figure", "figcaption", "dd", "dt",
})
# Tags whose text content is never document prose.
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "noscript"})


class TextRun:
    """A contiguous slice of the flat text and where it came from."""

    __slots__ = ("start", "end", "element_id", "is_link", "link_target")

    def __init__(
        self,
        start: int,
        end: int,
        element_id: str | None,
        is_link: bool,
        link_target: str | None,
    ) -> None:
        self.start = start
        self.end = end
        self.element_id = element_id
        self.is_link = is_link
        self.link_target = link_target


class NormalizedDoc:
    """Flat text plus an ordered run map for offset → (element_id, is_link)."""

    def __init__(
        self,
        text: str,
        runs: list[TextRun],
        id_offsets: dict[str, int] | None = None,
    ) -> None:
        self.text = text
        self.runs = runs
        self.id_offsets = id_offsets or {}

    def _run_at(self, offset: int) -> TextRun | None:
        """Return the run covering ``offset``, or the following run for a gap."""
        runs = self.runs
        lo, hi = 0, len(runs) - 1
        best: TextRun | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            run = runs[mid]
            if offset < run.start:
                best = run
                hi = mid - 1
            elif offset >= run.end:
                lo = mid + 1
            else:
                return run
        return best

    def context_at(self, offset: int) -> tuple[str | None, bool]:
        """Nearest element id and link flag covering ``offset``.

        Binary search over runs; if the offset lands in an inter-run gap (a
        synthesised newline), the following run's context is used.
        """
        run = self._run_at(offset)
        return (run.element_id, run.is_link) if run else (None, False)

    def is_link_at(self, offset: int) -> bool:
        return self.context_at(offset)[1]

    def element_id_at(self, offset: int) -> str | None:
        return self.context_at(offset)[0]

    def link_target_between(self, start: int, end: int) -> int | None:
        """Resolve the first same-document link target intersecting a text range."""
        for run in self.runs:
            if run.end <= start:
                continue
            if run.start >= end:
                break
            if run.link_target is not None:
                target = self.id_offsets.get(run.link_target)
                if target is not None:
                    return target
        return None


def _nearest_block(node) -> object:
    """Nearest block-level ancestor node (for newline boundaries).

    Compared by value equality (``==``/``!=``), never ``is``: selectolax returns
    a fresh wrapper object on every ``.parent`` access, so two text runs in the
    same block are identity-distinct but value-equal. Using ``is`` here injects a
    newline before every run and shreds inline-formatted phrases / inline-XBRL.
    """
    p = node.parent
    while p is not None:
        if p.tag in _BLOCK_TAGS:
            return p
        p = p.parent
    return None


def _is_hidden(node) -> bool:
    tag = (node.tag or "").lower()
    if tag == "ix:hidden" or tag.startswith("ix:header"):
        return True
    style = node.attributes.get("style")
    if style and "display:none" in style.lower().replace(" ", ""):
        return True
    return False


def _ancestor_context(node) -> tuple[str | None, bool, bool, str | None]:
    """Return (nearest_element_id, inside_anchor_link, skip, link_target).

    ``skip`` is True when the text is script/style boilerplate or hidden
    (``display:none`` / inline-XBRL ``ix:hidden`` cover-page metadata).
    """
    element_id: str | None = None
    is_link = False
    skip = False
    link_target: str | None = None
    p = node.parent
    while p is not None:
        if p.tag in _SKIP_TAGS or _is_hidden(p):
            skip = True
        if element_id is None:
            eid = p.attributes.get("id")
            if eid:
                element_id = eid
        if p.tag == "a" and p.attributes.get("href") is not None:
            is_link = True
            href = p.attributes.get("href") or ""
            if link_target is None and href.startswith("#") and len(href) > 1:
                link_target = unquote(href[1:])
        p = p.parent
    return element_id, is_link, skip, link_target


def html_to_text(html: str) -> NormalizedDoc:
    """Flatten HTML to text, recording an element-id/link run map.

    ``\\xa0`` (nbsp) is normalised to a plain space (1:1, offsets preserved) so
    ``\\s`` matching behaves; other characters are left verbatim so citations
    round-trip. A newline is inserted at every block-level boundary.
    """
    tree = HTMLParser(html)
    root = tree.root
    if root is None:
        return NormalizedDoc("", [], {})

    parts: list[str] = []
    runs: list[TextRun] = []
    id_offsets: dict[str, int] = {}
    pos = 0
    prev_block: object = object()  # sentinel: first real block differs

    for node in root.traverse(include_text=True):
        if node.tag in ("br", "hr"):
            # Void break elements carry no text node; realize the line break here so
            # ``<b>Overview.</b><br/>Item 7A. ...`` does not merge onto one line.
            if pos > 0 and not parts[-1].endswith("\n"):
                parts.append("\n")
                pos += 1
            continue
        if node.tag != "-text":
            element_id = node.attributes.get("id")
            _, _, ancestor_skip, _ = _ancestor_context(node)
            if (
                element_id
                and element_id not in id_offsets
                and node.tag not in _SKIP_TAGS
                and not _is_hidden(node)
                and not ancestor_skip
            ):
                id_offsets[element_id] = pos
            continue
        raw = node.text_content
        if not raw:
            continue
        element_id, is_link, skip, link_target = _ancestor_context(node)
        if skip:
            continue
        block = _nearest_block(node)
        if block != prev_block and pos > 0 and not parts[-1].endswith("\n"):
            parts.append("\n")
            pos += 1
        prev_block = block
        # SEC filings commonly use NBSP, thin-space, en-space, and narrow-NBSP
        # between header tokens (for example ``Item\u20095.02``). Normalize every
        # Unicode space-separator to one ASCII space; this remains 1:1 so provenance
        # offsets continue to round-trip exactly.
        text = "".join(" " if unicodedata.category(char) == "Zs" else char for char in raw)
        start = pos
        parts.append(text)
        pos += len(text)
        runs.append(TextRun(start, pos, element_id, is_link, link_target))

    return NormalizedDoc("".join(parts), runs, id_offsets)


def normalize_whitespace_line(line: str) -> str:
    """Collapse runs of whitespace to single spaces and strip (for title text)."""
    return " ".join(unicodedata.normalize("NFKC", line).split())
