"""Deterministic markdown digest renderer (CLAUDE.md §15).

Reads ONLY the DB — every digest is reproducible with no LLM calls at render time.
Sections, in order: header · critical red flags · what changed · thesis impact ·
verified numbers · open questions · boring filings · (‑‑signals) shadow signals.

Design posture matches the product: postures not trade actions; silence on boring
filings is a feature; every rendered number traces to a persisted computation or a
verbatim evidence snippet; missing P2/P3 degrades gracefully.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from finwatch.db.repositories import Company, Filing, Holding, Repo, SignalShadowLog
from finwatch.llm.schemas import Claim, P1Output, P2Output, P3Output
from finwatch.metrics.envelope import MetricResult

# Starter-set metrics surfaced in the digest (CLAUDE.md §9 "conservative surface").
_STARTER = ("revenue_growth", "net_income_trend", "cfo_trend", "liquidity_basics",
            "share_count_change", "simple_leverage")
_STARTER_LABELS = {
    "revenue_growth": "Revenue growth", "net_income_trend": "Net income trend",
    "cfo_trend": "Operating cash flow", "liquidity_basics": "Liquidity",
    "share_count_change": "Share count Δ", "simple_leverage": "Leverage",
}
_CRITICAL_SEVERITIES = frozenset({"critical", "high"})
# P2 transmission channels (skip C8, the driver-type label, and any "not implicated").
_CHANNEL_LABELS = {
    "C1": "revenue", "C2": "margins", "C3": "capital structure", "C4": "cash/working capital",
    "C5": "competitive position", "C6": "governance", "C7": "cross-holding spillover",
}


@dataclass
class DigestRender:
    markdown: str
    accessions: list[str] = field(default_factory=list)


@dataclass
class _FilingView:
    filing: Filing
    company: Company | None
    holding: Holding | None
    p1: P1Output | None
    p2: P2Output | None
    p3: P3Output | None
    claims: dict[str, Claim]             # claim_id -> P1 claim (original ids + provenance)
    manual_review: bool

    @property
    def ticker(self) -> str:
        return self.company.ticker if self.company else self.filing.cik

    @property
    def severity(self) -> str:
        return self.p1.classification.overall_severity if self.p1 else "unanalyzed"

    @property
    def is_critical(self) -> bool:
        return bool(self.p1) and (
            self.severity in _CRITICAL_SEVERITIES or bool(self.p1.red_flags))


def _has_impact(view: _FilingView) -> bool:
    """True when P2 found at least one non-``no_impact`` record for this filing."""
    return view.p2 is not None and any(
        rec.impact_class != "no_impact" for rec in view.p2.records_affected)


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


def _metric_summary(r: MetricResult) -> str:
    """One-line human summary of a computed starter metric."""
    c = r.components
    m = r.metric
    if m == "revenue_growth":
        return f"{_pct(c.get('yoy'))} YoY (TTM revenue {_usd(c.get('ttm_revenue'))})"
    if m in ("net_income_trend", "cfo_trend"):
        direction = c.get("four_quarter_direction", "?")
        return f"{_pct(c.get('yoy'))} YoY · 4-quarter direction {direction}"
    if m == "liquidity_basics":
        parts = [f"cash {_usd(c.get('cash'))}", f"net debt {_usd(c.get('net_debt'))}"]
        if c.get("current_ratio") is not None:
            parts.append(f"current ratio {_num(c['current_ratio'])}")
        return " · ".join(parts)
    if m == "share_count_change":
        drift = "buyback" if (r.value or 0) < 0 else "dilution" if (r.value or 0) > 0 else "flat"
        return f"{_pct(r.value)} YoY ({drift})"
    if m == "simple_leverage":
        parts = []
        if c.get("net_debt_to_ebitda") is not None:
            parts.append(f"net debt/EBITDA {_num(c['net_debt_to_ebitda'])}×")
        if c.get("interest_coverage") is not None:
            parts.append(f"interest coverage {_num(c['interest_coverage'])}×")
        return " · ".join(parts) or "computed"
    return _num(r.value) if r.value is not None else "computed"


def _edgar_url(filing: Filing) -> str:
    """Best-effort EDGAR link: the stored primary document, else the filing index."""
    if filing.primary_doc_url:
        return filing.primary_doc_url
    accn_nodash = filing.accession_number.replace("-", "")
    try:
        cik = str(int(filing.cik))
    except ValueError:
        cik = filing.cik
    return (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/"
            f"{filing.accession_number}-index.htm")


# ---------------------------------------------------------------- loading ----
def _load_view(repo: Repo, filing: Filing) -> _FilingView:
    def _stage(stage: str):
        a = repo.latest_analysis(filing.accession_number, stage)
        return a

    p1a, p2a, p3a = _stage("P1"), _stage("P2"), _stage("P3")
    p1 = P1Output.model_validate_json(p1a.output_json) if p1a else None
    p2 = P2Output.model_validate_json(p2a.output_json) if p2a else None
    p3 = P3Output.model_validate_json(p3a.output_json) if p3a else None
    # Claims come from the parsed P1 output (original claim_ids + full provenance); the
    # persisted analysis_claims rows namespace the ids, which would not match red_flag refs.
    claims = {c.claim_id: c for c in p1.claims} if p1 else {}
    manual = False
    if p1a:
        manual = any(v.verdict == "fail" and v.severity == "blocking"
                     for v in repo.list_verification_results(p1a.id))
    return _FilingView(filing, repo.get_company(filing.cik),
                       repo.get_holding_by_cik(filing.cik), p1, p2, p3, claims, manual)


def _evidence_snippet(view: _FilingView, claim_ids: list[str]) -> str | None:
    """First verbatim evidence snippet backing any of the given claim ids."""
    for cid in claim_ids:
        claim = view.claims.get(cid)
        if claim and claim.claim_type == "evidence" and claim.provenance:
            snippet = claim.provenance.snippet
            if snippet:
                return snippet
    return None


def _in_window(filing: Filing, since: str | None, until: str | None) -> bool:
    day = _date(filing.filed_at)
    if since and day < since[:10]:
        return False
    if until and day > until[:10]:
        return False
    return True


# ------------------------------------------------------------ section render -
def _header(views: list[_FilingView], holdings: list[Holding],
            since: str | None, until: str | None) -> list[str]:
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
        posture = (v.p3.review_posture if v.p3
                   else "watch — company-level read, no signal" if v.holding and not v.holding.owned
                   else v.severity)
        out.append(f"### {v.ticker} — {v.filing.form_type} filed {_date(v.filing.filed_at)} "
                   f"· {v.severity.upper()} · {posture}")
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
            out.append(f"### {rec.ticker} ({rec.impact_class}) — via {v.ticker} "
                       f"{v.filing.form_type} {_date(v.filing.filed_at)}")
            out.append(rec.net_read.text)
            channels = _implicated_channels(rec.channels)
            if channels:
                out.append(f"- **Channels:** {', '.join(channels)}")
            out.append(f"- **Guidance:** {rec.guidance_direction} · "
                       f"**Liquidity:** {rec.liquidity_read} · **Net:** {rec.net_direction}")
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
    _NO_THESIS = ("No thesis provided. I can still monitor critical red flags, filing changes, "
                  "and financial deterioration, but I cannot say whether this weakens your "
                  "original reason for owning the stock.")
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
        results = [MetricResult.model_validate_json(comps[n].result_json)
                   for n in _STARTER if n in comps]
        if not any(r.status.value == "computed" for r in results):
            # Nothing computed for this issuer — one honest line beats six "unavailable" rows.
            if results:
                any_shown = True
                out.append(f"- **{h.ticker}:** no verified financials yet "
                           f"(XBRL facts insufficient or not yet ingested).")
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
        label = _STARTER_LABELS.get(r.metric, r.metric)
        if r.status.value == "computed":
            rows.append(f"| {label} | {_metric_summary(r)} | `{r.formula_version}` | ✓ |")
        elif r.status.value == "not_applicable":
            reason = r.not_applicable_reason or "not applicable for this issuer"
            rows.append(f"| {label} | n/a — {reason} | `{r.formula_version}` | — |")
        else:  # unavailable
            missing = ", ".join(r.unavailable_missing) or "missing data"
            rows.append(f"| {label} | unavailable — {missing} | `{r.formula_version}` | — |")
    return rows


def _open_questions_section(views: list[_FilingView]) -> list[str]:
    out = ["## Open questions", ""]
    items: list[str] = []
    for v in views:
        if v.p1 is not None:
            for gap in v.p1.gaps:
                items.append(f"- {v.ticker}: {gap}")
        if v.p3 is not None:
            for sk in v.p3.rules_skipped:
                items.append(f"- {v.ticker}: rule {sk.rule} not evaluated — {sk.reason}")
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


def _shadow_section(repo: Repo, accessions: set[str],
                    views_by_accn: dict[str, _FilingView]) -> list[str]:
    out = [
        "## Shadow signals",
        "",
        "> ⚠ **Unvalidated shadow output — educational only, not a trade instruction.** "
        "These hypothetical signals are logged to build an auditable track record; they are "
        "off by default and shown only with `--signals`.",
        "",
    ]
    rows = [r for r in repo.list_shadow_log() if r.accession_number in accessions]
    if not rows:
        out.append("_No shadow evaluations in this window._")
        out.append("")
        return out
    for row in rows:
        out += _shadow_block(row, views_by_accn.get(row.accession_number))
    return out


def _shadow_block(row: SignalShadowLog, view: _FilingView | None) -> list[str]:
    out = [f"### {row.ticker} — hypothetical signal: **{row.hypothetical_signal}** "
           f"(posture {row.review_posture})"]
    try:
        fired = json.loads(row.rules_fired_json)
    except json.JSONDecodeError:
        fired = []
    if fired:
        out.append(f"- Rules fired: {', '.join(fired)}")
    if view is not None and view.p3 is not None:
        p3 = view.p3
        out.append(f"- Rationale: {p3.rationale}")
        if p3.counter_evidence:
            out.append(f"- Counter-evidence: {p3.counter_evidence}")
        if p3.what_would_change_this:
            out.append("- What would change this: " + "; ".join(p3.what_would_change_this))
    out.append("")
    return out


# ------------------------------------------------------------------- entry ---
def render_digest(
    repo: Repo,
    *,
    since: str | None = None,
    until: str | None = None,
    include_signals: bool = False,
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
    if include_signals:
        accns = {v.filing.accession_number for v in analyzed}
        lines += _shadow_section(repo, accns, {v.filing.accession_number: v for v in analyzed})

    from finwatch.core.types import DISCLAIMER
    lines += ["---", "", f"_{DISCLAIMER}_", ""]

    return DigestRender(markdown="\n".join(lines),
                        accessions=[v.filing.accession_number for v in views])
