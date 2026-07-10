"""Deterministic Markdown serialization of the canonical presentation DTO.

The presentation service is the sole database-to-content projection.  This module
does not load analyses, claims, or computations independently: it only serializes a
``BriefView`` and gathers filing accession metadata for the persisted digest record.
That keeps the browser and Markdown surfaces on the same fail-closed content path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finwatch.db.repositories import Repo
from finwatch.metrics.catalog import STARTER_METRIC_LABELS, STARTER_METRICS
from finwatch.metrics.envelope import MetricResult, MetricsBundle
from finwatch.presentation.formatting import format_metric_value
from finwatch.presentation.models import (
    BriefView,
    FilingDigestEntry,
    IssuerMetricsView,
)


@dataclass
class DigestRender:
    markdown: str
    accessions: list[str] = field(default_factory=list)


def _single_line(value: str) -> str:
    """Keep untrusted verbatim evidence inside one Markdown list item."""
    return " ".join(value.splitlines()).strip()


def _markdown_text(value: str) -> str:
    """Render data as text, never as caller-supplied Markdown or raw HTML."""
    escaped = _single_line(value).replace("\\", "\\\\")
    for char in ("`", "*", "_", "[", "]"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped.replace("<", "&lt;").replace(">", "&gt;")


def _table_cell(value: str) -> str:
    """Escape only Markdown-table delimiters; content remains human-readable."""
    return _markdown_text(value).replace("|", r"\|")


def _code_cell(value: str) -> str:
    """Keep deterministic identifiers readable inside a Markdown code span."""
    return _single_line(value).replace("`", "").replace("|", r"\|")


def _header(brief: BriefView) -> list[str]:
    owned = ", ".join(brief.portfolio.owned) or "none"
    watching = ", ".join(brief.portfolio.watching)
    tracked = owned if not watching else f"{owned}  ·  watching: {watching}"
    return [
        "# finwatch digest",
        "",
        f"> {_markdown_text(brief.answer)}",
        "",
        f"- **Period covered:** {_markdown_text(brief.period.covered)}",
        f"- **Holdings tracked:** {_markdown_text(tracked)}",
        (
            f"- **Filings in window:** {brief.period.filings_in_window} "
            f"· **Analyzed:** {brief.period.analyzed_filings}"
        ),
        "",
    ]


def _withheld_section(entries: list[FilingDigestEntry]) -> list[str]:
    if not entries:
        return []
    out = ["## Withheld analyses", ""]
    for entry in entries:
        reason = entry.withheld_reason or (
            "LLM-derived analysis withheld because deterministic verification did not pass."
        )
        out.append(
            f"- [{_markdown_text(entry.ticker)} — {_markdown_text(entry.form)} filed "
            f"{_markdown_text(entry.filed)}]({entry.edgar_url}) — "
            f"{_markdown_text(reason)}"
        )
    out.append("")
    return out


def _findings_section(entries: list[FilingDigestEntry]) -> list[str]:
    out = [
        "## AI-selected changes (evidence verified)",
        "",
        "_The model selects and summarizes importance. Deterministic checks prove that "
        "each displayed quotation is exact; they do not prove the model's interpretation._",
        "",
    ]
    material = [entry for entry in entries if entry.findings and not entry.manual_review]
    if not material:
        out.extend(["_No evidence-backed changes were selected in this window._", ""])
        return out

    for entry in material:
        out.append(
            f"### [{_markdown_text(entry.ticker)} — {_markdown_text(entry.form)} filed "
            f"{_markdown_text(entry.filed)}]({entry.edgar_url})"
        )
        for finding in entry.findings:
            out.append(f"- **{_markdown_text(finding.headline)}** _(AI: {finding.severity})_")
            for evidence in finding.evidence:
                out.append(
                    f"  - Evidence — [{evidence.section_key}]({evidence.edgar_url}): "
                    f"“{_markdown_text(evidence.quote)}”"
                )
        out.append("")
    return out


def _verified_numbers_section(issuers: list[IssuerMetricsView]) -> list[str]:
    out = [
        "## Verified numbers",
        "",
        "_Computed by versioned deterministic formulas from SEC XBRL facts "
        "(never by the LLM). ✓ = computed; — = not applicable or unavailable._",
        "",
    ]
    shown = False
    for issuer in issuers:
        if issuer.empty:
            shown = True
            out.extend(
                [f"- **{_markdown_text(issuer.ticker)}:** {_markdown_text(issuer.empty)}", ""]
            )
            continue
        if not issuer.rows:
            continue
        shown = True
        out.extend(
            [
                f"### {_markdown_text(issuer.ticker)}",
                "| Metric | Value | Computed as of | Formula | ✓ |",
                "|---|---|---|---|---|",
            ]
        )
        for row in issuer.rows:
            marker = "✓" if row.state == "computed" else "—"
            out.append(
                f"| {_table_cell(row.metric)} | {_table_cell(row.value)} | "
                f"{_table_cell(row.effective_as_of)} | `{_code_cell(row.formula)}` | "
                f"{marker} |"
            )
        out.append("")
    if not shown:
        out.extend(["_No verified financials available yet._", ""])
    return out


def _open_questions_section(questions: list[str]) -> list[str]:
    out = ["## Open questions", ""]
    if questions:
        out.extend(f"- {_markdown_text(question)}" for question in questions)
    else:
        out.append("_None._")
    out.append("")
    return out


def _boring_section(summary: str | None) -> list[str]:
    if not summary:
        return []
    return ["## Boring filings", "", _markdown_text(summary), ""]


def render_brief_markdown(brief: BriefView) -> str:
    """Serialize the exact canonical browser DTO to deterministic Markdown."""
    lines: list[str] = []
    lines.extend(_header(brief))
    lines.extend(_withheld_section(brief.withheld_filings))
    lines.extend(_findings_section(brief.filings))
    lines.extend(_verified_numbers_section(brief.verified_numbers))
    lines.extend(_open_questions_section(brief.open_questions))
    lines.extend(_boring_section(brief.boring_filings))
    if brief.tracked_but_unanalyzed:
        lines.extend(
            [
                "_Tracked companies have no analyzed filings yet. Sync and run analysis to begin._",
                "",
            ]
        )
    lines.extend(["---", "", f"_{brief.disclaimer}_", ""])
    return "\n".join(lines)


def _accessions_in_window(
    repo: Repo, since: str | None, until: str | None
) -> list[str]:
    """Return deterministic filing metadata for ``Digest.filings_json`` only."""
    filings = []
    for filing in repo.list_filings():
        filed = (filing.filed_at or "")[:10]
        if since and filed < since[:10]:
            continue
        if until and filed > until[:10]:
            continue
        filings.append(filing)
    filings.sort(key=lambda row: (row.filed_at, row.accession_number), reverse=True)
    return [filing.accession_number for filing in filings]


def render_digest(
    repo: Repo,
    *,
    since: str | None = None,
    until: str | None = None,
) -> DigestRender:
    """Build the canonical brief once and serialize that exact DTO to Markdown."""
    from finwatch.presentation.service import PresentationService

    brief = PresentationService(repo).brief(since=since, until=until)
    return DigestRender(
        markdown=render_brief_markdown(brief),
        accessions=_accessions_in_window(repo, since, until),
    )


def metric_view_rows(bundle: MetricsBundle) -> list[tuple[str, str, str, str]]:
    """Compatibility serializer for the internal ``finwatch metrics`` CLI.

    The launch digest never calls this helper; it consumes canonical ``MetricRowView``
    objects.  Keeping it here avoids widening this refactor into the CLI while still
    sharing the starter catalog and deterministic formatting implementation.
    """
    by_name = {result.metric: result for result in bundle.all_results()}
    results: list[MetricResult] = [
        by_name[name] for name in STARTER_METRICS if name in by_name
    ]
    rows: list[tuple[str, str, str, str]] = []
    for result in results:
        label = STARTER_METRIC_LABELS.get(result.metric, result.metric)
        if result.status.value == "computed":
            rows.append(
                (label, format_metric_value(result), result.formula_version, "✓")
            )
        elif result.status.value == "not_applicable":
            reason = result.not_applicable_reason or "not applicable for this issuer"
            rows.append((label, f"n/a — {reason}", result.formula_version, "—"))
        else:
            missing = ", ".join(result.unavailable_missing) or "missing data"
            rows.append(
                (label, f"unavailable — {missing}", result.formula_version, "—")
            )
    return rows
