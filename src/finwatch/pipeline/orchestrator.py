"""Pipeline orchestrator: P0 → P1 → metrics → P2 → verify (per filing).

Deterministic control flow around the (stochastic) LLM stages. P1 + metrics run for
every filing; P2 runs when the filing is material (overall_severity ≥ MEDIUM or any
red flag); the verifier gates the result and, on a blocking FAIL, the §14 policy
flags manual review. P3 (the signal engine) is Phase 6 — V3 is therefore skipped here.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finwatch.core.types import DISCLAIMER
from finwatch.db.repositories import Filing, Repo
from finwatch.llm.schemas import P1Output, P2Output
from finwatch.llm.stages import P1Extractor, P2Explainer
from finwatch.metrics.envelope import MetricsBundle
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.adapters import to_extraction_summary, to_impact_summary, to_record
from finwatch.preprocess.diff import RiskFactorDiff
from finwatch.preprocess.preprocessor import Preprocessor
from finwatch.signals.engine import SignalEngine, SignalResult
from finwatch.verify.checks import EvidenceClaim, VerificationReport, VerifyBundle
from finwatch.verify.orchestrator import (
    fact_values_from_repo,
    persist_report,
    run_with_regeneration,
    section_texts_from_repo,
)

_P2_MATERIAL = frozenset({"critical", "high", "medium"})


@dataclass
class FilingAnalysis:
    accession_number: str
    ticker: str
    p1: P1Output
    p1_analysis_id: int
    metrics: MetricsBundle
    p2: P2Output | None
    verification: VerificationReport
    manual_review: bool
    signal: SignalResult | None = None
    starter_metrics: list = field(default_factory=list)


def p2_gate(p1: P1Output) -> bool:
    """P2 runs on material filings: overall_severity ≥ MEDIUM, or a non-empty red-flag
    register regardless of severity (CLAUDE.md §12)."""
    return p1.classification.overall_severity in _P2_MATERIAL or bool(p1.red_flags)


def risk_diff_to_dict(diff: RiskFactorDiff) -> dict:
    def para(p):
        return {"text": p.text, "char_start": p.char_start, "char_end": p.char_end}

    return {
        "added": [para(p) for p in diff.added],
        "removed": [para(p) for p in diff.removed],
        "modified": [{"prior": para(m.prior), "current": para(m.current),
                      "similarity": m.similarity} for m in diff.modified],
    }


def assemble_verify_bundle(
    p1: P1Output,
    p2: P2Output | None,
    metrics: MetricsBundle,
    section_texts: dict[str, str],
    fact_values: list[float],
    *,
    disclaimer: str = DISCLAIMER,
    decision=None,
    record=None,
    extraction=None,
    impact=None,
) -> VerifyBundle:
    """Build the VerifyBundle from the analysis. The rendered text is the verifiable
    analysis summary (red flags, net reads, verbatim evidence snippets) — its numbers
    come from evidence snippets and metrics, which are V1's candidate pool. When a P3
    decision is supplied, V3 re-derives it from the same inputs to audit the signal."""
    lines: list[str] = []
    for rf in p1.red_flags:
        lines.append(f"Red flag: {rf.flag} ({rf.severity}).")
    for mi in p1.material_items:
        lines.append(f"{mi.headline} [{mi.event_type}]")
    if p2 is not None:
        for rec in p2.records_affected:
            lines.append(rec.net_read.text)
    evidence: list[EvidenceClaim] = []
    for c in p1.claims:
        if c.claim_type == "evidence" and c.provenance is not None:
            pv = c.provenance
            lines.append(pv.snippet)
            evidence.append(EvidenceClaim(
                claim_id=c.claim_id, accession_number=pv.accession_number,
                section_key=pv.section_key, char_start=pv.char_start, char_end=pv.char_end,
                snippet=pv.snippet, text_sha256=None,
            ))
    return VerifyBundle(
        rendered_text="\n".join(lines),
        metrics=metrics,
        fact_store_values=fact_values,
        evidence_claims=evidence,
        section_texts=section_texts,
        decision=decision,      # present for owned records -> V3 re-derivation runs
        record=record,
        extraction=extraction,
        impact=impact,
        trade_action=None,
        disclaimer_text=disclaimer,
    )


class Orchestrator:
    def __init__(
        self,
        repo: Repo,
        preprocessor: Preprocessor,
        p1: P1Extractor,
        p2: P2Explainer,
        metrics_service: MetricsService,
        *,
        companyfacts_provider: Callable[[str], dict],
        signal_engine: SignalEngine | None = None,
        now_fn: Callable[[], str] | None = None,
        disclaimer: str = DISCLAIMER,
    ) -> None:
        self.repo = repo
        self.preprocessor = preprocessor
        self.p1 = p1
        self.p2 = p2
        self.metrics_service = metrics_service
        self.companyfacts_provider = companyfacts_provider
        self.signal_engine = signal_engine
        self._now_fn = now_fn or (lambda: datetime.now(UTC).isoformat())
        self.disclaimer = disclaimer

    def process_html(
        self, *, filing: Filing, html: str, as_of: str, records: list,
    ) -> FilingAnalysis:
        company = self.repo.get_company(filing.cik)
        ticker = company.ticker if company else filing.cik

        # --- P0: preprocess -> canonical sections + risk-factor diff -------
        pp = self.preprocessor.preprocess_html(
            accession_number=filing.accession_number, cik=filing.cik,
            form_type=filing.form_type, filed_at=filing.filed_at,
            period_of_report=filing.period_of_report, html=html,
        )
        sections = {
            s.section_key: {
                "text": s.text, "char_start": s.char_start, "char_end": s.char_end,
                "html_element_id": s.html_element_id, "is_furnished": bool(s.is_furnished),
            }
            for s in self.repo.list_filing_sections(filing.accession_number)
        }
        filing_meta = {
            "cik": filing.cik, "ticker": ticker,
            "company_name": company.name if company else None,
            "form_type": filing.form_type, "filed_at": filing.filed_at,
            "period_of_report": filing.period_of_report,
            "accession_number": filing.accession_number,
            "is_amendment": bool(filing.is_amendment),
            "amends_accession": pp.amends_accession,
        }
        rf_diff = risk_diff_to_dict(pp.risk_factor_diff) if pp.risk_factor_diff else None

        # --- P1: extract ---------------------------------------------------
        p1_out, p1_aid, _ = self.p1.run(
            filing_meta=filing_meta, sections=sections, risk_factor_diff=rf_diff,
        )

        # --- metrics -------------------------------------------------------
        # Persist to `computations` so the digest renders "Verified numbers" straight
        # from the DB (deterministic, no LLM at render time — CLAUDE.md §15).
        metrics = self.metrics_service.compute(filing.cik, as_of=as_of)
        self.metrics_service.persist(ticker, metrics, as_of)

        # --- P2: portfolio impact (material filings only) ------------------
        p2_out = None
        if p2_gate(p1_out) and records:
            p2_out, _, _ = self.p2.run(
                extraction=p1_out.model_dump(), records=records,
                accession_number=filing.accession_number, ticker=ticker,
            )

        # --- P3: signal engine (owned records only; shadow mode) ----------
        signal = None
        decision = record = extraction_sum = impact_sum = None
        holding = self.repo.get_holding_by_cik(filing.cik)
        if self.signal_engine is not None and holding is not None and holding.owned:
            record = to_record(holding, metrics)
            extraction_sum = to_extraction_summary(p1_out)
            impact_sum = to_impact_summary(p2_out, ticker)
            signal = self.signal_engine.run(
                record=record, extraction=extraction_sum, impact=impact_sum, metrics=metrics,
                accession_number=filing.accession_number, ticker=ticker, as_of=as_of,
            )
            decision = signal.decision

        # --- verify (V1/V4/V5; + V3 re-derivation when a P3 decision exists) --
        # The verifier gates the LLM OUTPUT — the failure modes regeneration can fix.
        # store/sector (→ V2 accounting identities) are deliberately NOT passed: V2
        # validates the XBRL *data*, which re-running the LLM can never repair, and the
        # Tier 1 V2b cash tie-out systematically false-fails on quarterly-latest filings
        # (it compares instant_pair("cash") — the latest quarter-ends — against
        # latest_annual("cash_change"), a different period). V2 belongs to a separate
        # data-quality audit, not the per-filing analysis gate. (Flagged to the operator.)
        bundle = assemble_verify_bundle(
            p1_out, p2_out, metrics,
            section_texts_from_repo(self.repo, filing.accession_number),
            fact_values_from_repo(self.repo, filing.cik),
            disclaimer=self.disclaimer,
            decision=decision, record=record, extraction=extraction_sum, impact=impact_sum,
        )
        # A recorded/deterministic response cannot self-repair, so give up immediately
        # and route to manual review; a live pipeline supplies a real stage re-run here.
        outcome = run_with_regeneration(bundle, lambda _r, _n: None)
        persist_report(self.repo, p1_aid, outcome.report, created_at=self._now_fn())

        return FilingAnalysis(
            accession_number=filing.accession_number, ticker=ticker, p1=p1_out,
            p1_analysis_id=p1_aid, metrics=metrics, p2=p2_out,
            verification=outcome.report, manual_review=outcome.manual_review, signal=signal,
        )
