"""Deterministic markdown digest renderer (CLAUDE.md §15).

Reads ONLY the DB — every digest is reproducible with no LLM calls at render time.
Sections, in order: header · critical red flags · what changed · thesis impact ·
verified numbers · open questions · boring filings.

Silence on boring filings is a feature; every rendered number traces to a persisted
computation or a verbatim evidence snippet; missing P2 degrades gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finwatch.db.repositories import Filing, Holding, Repo
from finwatch.llm.schemas import P1Output
from finwatch.metrics.catalog import STARTER_METRIC_LABELS, STARTER_METRICS
from finwatch.metrics.envelope import MetricResult, MetricsBundle
from finwatch.presentation.formatting import format_metric_value as _format_metric_value
from finwatch.presentation.projection import (
    FilingProjection as _FilingView,
)
from finwatch.presentation.projection import (
    evidence_snippet as _projection_evidence_snippet,
)
from finwatch.presentation.projection import (
    has_impact as _projection_has_impact,
)
from finwatch.presentation.projection import (
    in_window as _projection_in_window,
)
from finwatch.presentation.projection import (
    load_filing_projection as _load_view,
)

_CRITICAL_SEVERITIES = frozenset({"critical", "high"})
# P2 transmission channels (skip C8, the driver-type label, and any "not implicated").
_CHANNEL_LABELS = {
    "C1": "revenue",
    "C2": "margins",
    "C3": "capital structure",
    "C4": "cash/working capital",
    "C5": "competitive position",
    "C6": "governance",
    "C7": "cross-holding spillover",
}


@dataclass
class DigestRender:
    markdown: str
    accessions: list[str] = field(default_factory=list)


def _has_impact(view: _FilingView) -> bool:
    """True when P2 found at least one non-``no_impact`` record for this filing."""
    return _projection_has_impact(view)


# --------------------------------------------------------------- formatting --
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign, a = ("-" if x < 0 else ""), abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}${a / div:.1f}{suf}"
    return f"{sign}${a:.0f}"


def _num(x: float | None, dp: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{dp}f}"


def _date(iso: str | None) -> str:
    return (iso or "")[:10]


def format_metric_value(r: MetricResult) -> str:
    """One-line human summary of a computed metric's value (shared by the digest table and
    the ``finwatch metrics`` CLI so the two never drift)."""
    return _format_metric_value(r)


def _edgar_url(filing: Filing) -> str:
    """Best-effort EDGAR link: the stored primary document, else the filing index."""
    if filing.primary_doc_url:
        return filing.primary_doc_url
    accn_nodash = filing.accession_number.replace("-", "")
    try:
        cik = str(int(filing.cik))
    except ValueError:
        cik = filing.cik
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/"
        f"{filing.accession_number}-index.htm"
    )


# ---------------------------------------------------------------- loading ----
def _evidence_snippet(view: _FilingView, claim_ids: list[str]) -> str | None:
    """First verbatim evidence snippet backing any of the given claim ids."""
    return _projection_evidence_snippet(view, claim_ids)


def _in_window(filing: Filing, since: str | None, until: str | None) -> bool:
    return _projection_in_window(filing, since, until)


# ------------------------------------------------------------ section render -
def _header(
    views: list[_FilingView], holdings: list[Holding], since: str | None, until: str | None
) -> list[str]:
    owned = sorted({h.ticker for h in holdings if h.owned})
    watch = sorted({h.ticker for h in holdings if not h.owned})
    period = f"{since or 'inception'} → {until or 'now'}"
    line_tickers = ", ".join(owned) or "none"
    if watch:
        line_tickers += f"  ·  watching: {', '.join(watch)}"
    return [
        "# finwatch digest",
        "",
        f"- **Period covered:** {period}",
        f"- **Holdings tracked:** {line_tickers}",
        f"- **Filings in window:** {len(views)}",
        "",
    ]


def _critical_section(critical: list[_FilingView]) -> list[str]:
    out = ["## Critical red flags", ""]
    if not critical:
        out += ["_None. No critical or high-severity findings in this window._", ""]
        return out
    for v in critical:
        out.append(
            f"### {v.ticker} — {v.filing.form_type} filed {_date(v.filing.filed_at)} "
            f"· {v.severity.upper()}"
        )
        for mi in v.p1.material_items:
            if mi.severity in _CRITICAL_SEVERITIES:
                out.append(f"- {mi.headline} _({mi.event_type})_")
        for rf in v.p1.red_flags:
            snippet = _evidence_snippet(v, rf.claim_ids)
            line = f"- **{rf.flag}** ({rf.severity})"
            if snippet:
                line += f" — [EDGAR]({_edgar_url(v.filing)}): “{snippet}”"
            out.append(line)
        if v.manual_review:
            out.append("- ⚠ manual review required — automated verification failed")
        out.append("")
    return out


def _what_changed_section(material: list[_FilingView]) -> list[str]:
    out = ["## What changed", ""]
    rows = [v for v in material if v.p2 is not None]
    if not rows:
        out += ["_No portfolio-relevant transmission analysis in this window._", ""]
        return out
    for v in rows:
        for rec in v.p2.records_affected:
            if rec.impact_class == "no_impact":
                continue
            out.append(
                f"### {rec.ticker} ({rec.impact_class}) — via {v.ticker} "
                f"{v.filing.form_type} {_date(v.filing.filed_at)}"
            )
            out.append(rec.net_read.text)
            channels = _implicated_channels(rec.channels)
            if channels:
                out.append(f"- **Channels:** {', '.join(channels)}")
            out.append(
                f"- **Guidance:** {rec.guidance_direction} · "
                f"**Liquidity:** {rec.liquidity_read} · **Net:** {rec.net_direction}"
            )
            rf = _risk_factor_highlights(v.p1)
            if rf:
                out.append(f"- **Risk-factor changes:** {rf}")
            out.append("")
    return out


def _implicated_channels(channels: dict) -> list[str]:
    out = []
    for key, label in _CHANNEL_LABELS.items():
        ch = channels.get(key)
        if not isinstance(ch, dict):
            continue
        direction = ch.get("direction")
        if direction in (None, "not_implicated", "neutral"):
            continue
        mag = ch.get("magnitude")
        out.append(f"{label} ({direction}{', ' + mag if mag else ''})")
    return out


def _risk_factor_highlights(p1: P1Output | None) -> str:
    if p1 is None or p1.risk_factor_findings is None:
        return ""
    f = p1.risk_factor_findings
    bits = []
    if f.added:
        bits.append(f"{len(f.added)} added")
    if f.removed:
        bits.append(f"{len(f.removed)} removed")
    if f.modified:
        bits.append(f"{len(f.modified)} modified")
    return ", ".join(bits)


def _thesis_section(material: list[_FilingView]) -> list[str]:
    out = ["## Thesis impact", ""]
    _NO_THESIS = (
        "No thesis provided. I can still monitor critical red flags, filing changes, "
        "and financial deterioration, but I cannot say whether this weakens your "
        "original reason for owning the stock."
    )
    seen = False
    for v in material:
        if v.p2 is None:
            continue
        for rec in v.p2.records_affected:
            if not rec.owned or rec.impact_class == "no_impact":
                continue
            seen = True
            holding = v.holding if (v.holding and v.holding.ticker == rec.ticker) else None
            if holding is not None and holding.thesis is None:
                out.append(f"- **{rec.ticker}:** {_NO_THESIS}")
            else:
                out.append(f"- **{rec.ticker}:** thesis {rec.thesis_check.verdict}")
    if not seen:
        out.append("_No owned position had an assessable thesis impact in this window._")
    out.append("")
    return out


def _verified_numbers_section(repo: Repo, holdings: list[Holding]) -> list[str]:
    out = [
        "## Verified numbers",
        "",
        "_Computed by versioned deterministic formulas from SEC XBRL facts (never by the LLM) "
        "and traceable to those facts. ✓ = a computed value; — = not applicable or data missing._",
        "",
    ]
    owned = [h for h in holdings if h.owned]
    any_shown = False
    for h in owned:
        comps = {c.tool: c for c in repo.latest_computations(h.ticker)}
        results = [
            MetricResult.model_validate_json(comps[n].result_json)
            for n in STARTER_METRICS
            if n in comps
        ]
        if not any(r.status.value == "computed" for r in results):
            # Nothing computed for this issuer — one honest line beats six "unavailable" rows.
            if results:
                any_shown = True
                out.append(
                    f"- **{h.ticker}:** no verified financials yet "
                    f"(XBRL facts insufficient or not yet ingested)."
                )
                out.append("")
            continue
        any_shown = True
        out.append(f"### {h.ticker}")
        out.append("| Metric | Value | Formula | ✓ |")
        out.append("|---|---|---|---|")
        out += _metric_rows(results)
        out.append("")
    if not any_shown:
        out.append("_No verified financials available yet (ingest XBRL facts to populate)._")
        out.append("")
    return out


def _metric_rows(results: list[MetricResult]) -> list[str]:
    rows = []
    for r in results:
        label = STARTER_METRIC_LABELS.get(r.metric, r.metric)
        if r.status.value == "computed":
            rows.append(f"| {label} | {format_metric_value(r)} | `{r.formula_version}` | ✓ |")
        elif r.status.value == "not_applicable":
            reason = r.not_applicable_reason or "not applicable for this issuer"
            rows.append(f"| {label} | n/a — {reason} | `{r.formula_version}` | — |")
        else:  # unavailable
            missing = ", ".join(r.unavailable_missing) or "missing data"
            rows.append(f"| {label} | unavailable — {missing} | `{r.formula_version}` | — |")
    return rows


def metric_view_rows(bundle: MetricsBundle) -> list[tuple[str, str, str, str]]:
    """Display rows ``(label, value, formula_version, ✓|—)`` for the ``finwatch metrics`` CLI.

    Selection and value formatting are shared with the digest; deferred research metrics are
    ignored even if a legacy caller supplies a broader bundle.
    """
    by_name = {r.metric: r for r in bundle.all_results()}
    results = [by_name[n] for n in STARTER_METRICS if n in by_name]
    rows: list[tuple[str, str, str, str]] = []
    for r in results:
        label = STARTER_METRIC_LABELS.get(r.metric, r.metric)
        if r.status.value == "computed":
            rows.append((label, format_metric_value(r), r.formula_version, "✓"))
        elif r.status.value == "not_applicable":
            reason = r.not_applicable_reason or "not applicable for this issuer"
            rows.append((label, f"n/a — {reason}", r.formula_version, "—"))
        else:  # unavailable
            missing = ", ".join(r.unavailable_missing) or "missing data"
            rows.append((label, f"unavailable — {missing}", r.formula_version, "—"))
    return rows


def _open_questions_section(views: list[_FilingView]) -> list[str]:
    out = ["## Open questions", ""]
    items: list[str] = []
    for v in views:
        if v.p1 is not None:
            for gap in v.p1.gaps:
                items.append(f"- {v.ticker}: {gap}")
        for check_id, detail in v.data_quality:
            items.append(f"- {v.ticker}: data-quality check {check_id} — {detail}")
        if v.manual_review:
            items.append(f"- {v.ticker}: automated verification failed — manual review required")
    if not items:
        out.append("_None._")
    else:
        out += items
    out.append("")
    return out


def _boring_section(boring: list[_FilingView]) -> list[str]:
    if not boring:
        return []
    listing = ", ".join(f"{v.ticker} {v.filing.form_type}" for v in boring)
    return [
        "## Boring filings",
        "",
        f"{len(boring)} routine filing(s) with no material findings ({listing}).",
        "",
    ]


# ------------------------------------------------------------------- entry ---
def render_digest(
    repo: Repo,
    *,
    since: str | None = None,
    until: str | None = None,
) -> DigestRender:
    """Render the markdown digest for filings filed within [since, until] from the DB."""
    holdings = repo.list_holdings()
    filings = [f for f in repo.list_filings() if _in_window(f, since, until)]
    filings.sort(key=lambda f: (f.filed_at, f.accession_number), reverse=True)
    views = [_load_view(repo, f) for f in filings]
    analyzed = [v for v in views if v.p1 is not None]

    # Exhaustive buckets so no analyzed filing is silently dropped (determinism doctrine):
    # a filing is shown in Critical (if critical), and/or What-changed (if it has renderable
    # portfolio impact), and otherwise falls to the single Boring line.
    critical = [v for v in analyzed if v.is_critical]
    impactful = [v for v in analyzed if _has_impact(v)]
    boring = [v for v in analyzed if not v.is_critical and not _has_impact(v)]

    lines: list[str] = []
    lines += _header(views, holdings, since, until)
    lines += _critical_section(critical)
    lines += _what_changed_section(impactful)
    lines += _thesis_section(impactful)
    lines += _verified_numbers_section(repo, holdings)
    lines += _open_questions_section(analyzed)
    lines += _boring_section(boring)
    from finwatch.core.types import DISCLAIMER

    lines += ["---", "", f"_{DISCLAIMER}_", ""]

    return DigestRender(
        markdown="\n".join(lines), accessions=[v.filing.accession_number for v in views]
    )
