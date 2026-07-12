"""Pipeline orchestrator: P0 → P1 → starter metrics → verify (per filing).

Deterministic control flow around one stochastic extraction stage. The verifier gates
the result and any blocking failure withholds the entire LLM-derived presentation.
P2/P3 research modules are deliberately outside the prototype launch path.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finwatch.core.types import DISCLAIMER
from finwatch.db.repositories import Filing, Repo
from finwatch.llm.schemas import P1Output
from finwatch.llm.stages import P1Extractor
from finwatch.metrics.envelope import MetricsBundle
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.progress import ProgressCallback, StageReporter
from finwatch.preprocess.diff import RiskFactorDiff
from finwatch.preprocess.preprocessor import Preprocessor
from finwatch.verify.checks import EvidenceClaim, VerificationReport, VerifyBundle, run_all
from finwatch.verify.orchestrator import persist_report, section_texts_from_repo


@dataclass
class FilingAnalysis:
    accession_number: str
    ticker: str
    p1: P1Output
    p1_analysis_id: int
    metrics: MetricsBundle
    verification: VerificationReport
    manual_review: bool
    starter_metrics: list = field(default_factory=list)

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
    metrics: MetricsBundle,
    section_texts: dict[str, str],
    *,
    disclaimer: str = DISCLAIMER,
) -> VerifyBundle:
    """Build the VerifyBundle from the analysis. The rendered text is the verifiable
    launch projection: qualitative headlines plus exact evidence quotes. V1 candidates
    are only those quotes and deterministic starter metrics; an unrelated XBRL fact can
    never legitimize an authored number. P3 is outside the launch pipeline, so V3 is
    intentionally not applicable."""
    assert disclaimer is not None, "verify bundle requires disclaimer_text (V5)"
    authored_lines = [finding.headline for finding in p1.findings]
    lines = list(authored_lines)
    evidence: list[EvidenceClaim] = []
    for finding_index, finding in enumerate(p1.findings):
        for evidence_index, pv in enumerate(finding.evidence):
            lines.append(pv.snippet)
            evidence.append(EvidenceClaim(
                claim_id=f"finding-{finding_index + 1}-evidence-{evidence_index + 1}",
                accession_number=pv.accession_number,
                section_key=pv.section_key, char_start=pv.char_start, char_end=pv.char_end,
                snippet=pv.snippet, text_sha256=None,
            ))
    return VerifyBundle(
        rendered_text="\n".join(lines),
        authored_text="\n".join(authored_lines),
        metrics=metrics,
        fact_store_values=[],
        evidence_claims=evidence,
        section_texts=section_texts,
        extraction_confidence=p1.extraction_confidence,
        extraction_gaps=list(p1.gaps),
        trade_action=None,
        disclaimer_text=disclaimer,
    )


class Orchestrator:
    def __init__(
        self,
        repo: Repo,
        preprocessor: Preprocessor,
        p1: P1Extractor,
        metrics_service: MetricsService,
        *,
        now_fn: Callable[[], str] | None = None,
        disclaimer: str = DISCLAIMER,
    ) -> None:
        self.repo = repo
        self.preprocessor = preprocessor
        self.p1 = p1
        self.metrics_service = metrics_service
        self._now_fn = now_fn or (lambda: datetime.now(UTC).isoformat())
        self.disclaimer = disclaimer

    def process_html(
        self,
        *,
        filing: Filing,
        html: str | None,
        as_of: str,
        resume: bool = True,
        on_stage: ProgressCallback | None = None,
    ) -> FilingAnalysis:
        company = self.repo.get_company(filing.cik)
        ticker = company.ticker if company else filing.cik
        reporter = StageReporter(
            self.repo, filing.accession_number, now_fn=self._now_fn, callback=on_stage
        )

        def reusable(stage: str, artifact: object | None) -> bool:
            if not resume or not artifact:
                return False
            state = self.repo.get_filing_stage(filing.accession_number, stage)
            return state is None or state.status in {"completed", "skipped"}

        def run_stage(stage: str, operation, diagnostics=None):
            reporter.running(stage)
            try:
                result = operation()
                details = diagnostics(result) if diagnostics else {}
                reporter.completed(stage, details)
                return result
            except Exception as exc:
                reporter.failed(stage, exc)
                raise

        # --- P0: preprocess -> canonical sections + risk-factor diff -------
        stored_sections = self.repo.list_filing_sections(filing.accession_number)
        if reusable("parse", stored_sections):
            pp = self.preprocessor.load_result(filing)
            reporter.completed(
                "parse",
                {
                    "detected_form": pp.form_family,
                    "sections_found": [section.section_key for section in pp.sections],
                    "resumed": True,
                },
                message="Parsed (reused stored sections)",
            )
        else:
            def parse():
                if html is None:
                    raise ValueError("parsing requires the downloaded primary document")
                result = self.preprocessor.preprocess_html(
                    accession_number=filing.accession_number,
                    cik=filing.cik,
                    form_type=filing.form_type,
                    filed_at=filing.filed_at,
                    period_of_report=filing.period_of_report,
                    html=html,
                )
                if not result.sections:
                    raise ValueError(
                        f"{filing.form_type} section routing produced no canonical sections"
                    )
                return result

            pp = run_stage(
                "parse",
                parse,
                lambda result: {
                    "detected_form": result.form_family,
                    "document_bytes": len((html or "").encode("utf-8")),
                    "sections_found": [section.section_key for section in result.sections],
                    "section_count": len(result.sections),
                },
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
        stored_p1 = self.repo.latest_analysis(filing.accession_number, "P1")
        if reusable("extract", stored_p1):
            assert stored_p1 is not None
            p1_out = P1Output.model_validate_json(stored_p1.output_json)
            p1_aid = stored_p1.id
            reporter.completed(
                "extract",
                {"analysis_id": p1_aid, "resumed": True},
                message="Extracted (reused stored analysis)",
            )
        else:
            p1_out, p1_aid, _ = run_stage(
                "extract",
                lambda: self.p1.run(
                    filing_meta=filing_meta,
                    sections=sections,
                    risk_factor_diff=rf_diff,
                ),
                lambda result: {
                    "analysis_id": result[1],
                    "severity": result[0].classification.overall_severity,
                    "finding_count": len(result[0].findings),
                },
            )

        # --- metrics -------------------------------------------------------
        # Persist to `computations` so the digest renders "Verified numbers" straight
        # from the DB (deterministic, no LLM at render time — CLAUDE.md §15).
        metrics_were_complete = resume and reporter.is_complete("metrics")

        def compute_metrics():
            result = self.metrics_service.compute(filing.cik, as_of=as_of)
            if not metrics_were_complete:
                self.metrics_service.persist(ticker, result, as_of)
            return result

        metrics = run_stage(
            "metrics",
            compute_metrics,
            lambda result: {
                "computed": sum(metric.computed for metric in result.all_results()),
                "result_count": len(result.all_results()),
                "resumed": metrics_were_complete,
            },
        )

        # --- verify: launch-output gate (V1/V4/V5) ---------------------------
        # A blocking failure withholds all LLM-derived output. There is no in-place
        # regeneration; a production retry is a fresh full attempt (pipeline/run.py).
        reporter.running("verify")
        bundle = assemble_verify_bundle(
            p1_out, metrics,
            section_texts_from_repo(self.repo, filing.accession_number),
            disclaimer=self.disclaimer,
        )
        core = run_all(bundle)

        # --- data-quality audit: V2 accounting identities --------------------
        # V2 validates the XBRL DATA (never repairable by re-running the LLM). V2a (A=L+E)
        # and V2c (Rev≥GP≥OpInc) run on every filing; V2b (cash tie-out) only on annual
        # filings (it compares a fiscal-year change).
        v2 = self.metrics_service.data_quality(
            filing.cik, as_of=as_of, form_type=filing.form_type)
        combined = list(core.results) + list(v2)
        blocking = any(c.verdict == "fail" and c.severity == "blocking" for c in combined)
        warns = any(c.verdict == "warn" for c in combined)
        report = VerificationReport(
            verdict="FAIL" if blocking else "PASS_WITH_WARNINGS" if warns else "PASS",
            results=combined)
        persist_report(self.repo, p1_aid, report, created_at=self._now_fn())
        reporter.completed("verify", {"verdict": report.verdict, "checks": len(report.results)})

        return FilingAnalysis(
            accession_number=filing.accession_number, ticker=ticker, p1=p1_out,
            p1_analysis_id=p1_aid, metrics=metrics,
            verification=report, manual_review=report.verdict == "FAIL",
        )
