"""Pipeline orchestrator: P0 → metrics → research harness → verify (per filing).

Deterministic control flow around one bounded stochastic research stage. Finding-local
failures are pruned inside the harness; only run-level failures withhold the filing.
P2/P3 research modules are deliberately outside the prototype launch path.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finwatch.core.types import DISCLAIMER
from finwatch.db.repositories import Filing, Repo, VerificationResult
from finwatch.llm.harness import HarnessTrace
from finwatch.llm.schemas import P1Output
from finwatch.llm.stages import P1Extractor
from finwatch.metrics.envelope import MetricsBundle
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.progress import ProgressCallback, StageReporter
from finwatch.preprocess.preprocessor import Preprocessor
from finwatch.verify.checks import EvidenceClaim, VerificationReport, VerifyBundle, run_all
from finwatch.verify.orchestrator import section_texts_from_repo


@dataclass
class FilingAnalysis:
    accession_number: str
    ticker: str
    p1: P1Output
    p1_analysis_id: int
    trace_analysis_id: int
    metrics: MetricsBundle
    verification: VerificationReport
    withheld: bool
    starter_metrics: list = field(default_factory=list)


def _verification_rows(
    report: VerificationReport, analysis_id: int, *, created_at: str
) -> list[VerificationResult]:
    return [
        VerificationResult(
            analysis_id=analysis_id,
            check_id=check.check_id,
            verdict=check.verdict,
            severity=check.severity,
            detail=check.detail or None,
            created_at=created_at,
        )
        for check in report.results
    ]


def _finalized_trace(
    trace: HarnessTrace,
    *,
    trace_analysis_id: int,
    p1: P1Output,
    sections: dict[str, dict],
    report: VerificationReport | None,
) -> HarnessTrace:
    """Freeze the safe, attempt-local certificate snapshot before persistence."""
    verification = [] if report is None else [
        {
            "check_id": row.check_id,
            "verdict": row.verdict.upper(),
            "severity": row.severity,
        }
        for row in report.results
    ]
    verifier_failed = report is not None and report.verdict == "FAIL"
    verification_incomplete = report is None
    withheld = verifier_failed or verification_incomplete
    evidence = []
    if not withheld:
        for finding in p1.findings:
            for row in finding.evidence:
                section = sections.get(row.section_key)
                text = section.get("text", "") if isinstance(section, dict) else ""
                evidence.append({
                    "finding_id": finding.finding_id,
                    "section_key": row.section_key,
                    "char_start": row.char_start,
                    "char_end": row.char_end,
                    "section_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                })
    publication_outcome = "withheld" if withheld else trace.research_outcome
    terminal_reason = (
        "verification_incomplete"
        if verification_incomplete
        else "verification_failed"
        if verifier_failed
        else trace.research_terminal_reason
    )
    tool_calls = trace.tool_calls
    if withheld:
        tool_calls = [row.model_copy(update={"arguments": {}}) for row in tool_calls]
    return HarnessTrace.model_validate({
        **trace.model_dump(mode="json"),
        "trace_analysis_id": trace_analysis_id,
        "publication_outcome": publication_outcome,
        "terminal_reason": terminal_reason,
        "verification_verdict": None if report is None else report.verdict,
        "verification_snapshot": verification,
        "publication_snapshot": {
            "classification": None if withheld else p1.classification.overall_severity,
            "evidence": evidence,
        },
        "published_finding_ids": (
            [] if withheld else [finding.finding_id for finding in p1.findings]
        ),
        "tool_calls": [row.model_dump(mode="json") for row in tool_calls],
    })


def finalize_attempt(
    repo: Repo,
    *,
    trace: HarnessTrace,
    trace_analysis_id: int,
    p1_analysis_id: int,
    p1: P1Output,
    sections: dict[str, dict],
    report: VerificationReport | None,
    processed_at: str,
) -> HarnessTrace:
    """Finalize one exact linked research attempt through the repository transaction."""
    final_trace = _finalized_trace(
        trace,
        trace_analysis_id=trace_analysis_id,
        p1=p1,
        sections=sections,
        report=report,
    )
    filing_status = (
        "failed"
        if report is None
        else "analyzed"
        if report.verdict == "FAIL"
        else "verified"
    )
    repo.finalize_p1_attempt(
        p1_analysis_id,
        trace_analysis_id,
        verification_results=(
            []
            if report is None
            else _verification_rows(report, p1_analysis_id, created_at=processed_at)
        ),
        finalized_trace_json=final_trace.model_dump_json(),
        filing_status=filing_status,
        processed_at=processed_at,
    )
    return final_trace

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
    for finding in p1.findings:
        for evidence_index, pv in enumerate(finding.evidence):
            lines.append(pv.snippet)
            evidence.append(EvidenceClaim(
                claim_id=f"{finding.finding_id}-evidence-{evidence_index + 1}",
                accession_number=pv.accession_number,
                section_key=pv.section_key, char_start=pv.char_start, char_end=pv.char_end,
                snippet=pv.snippet, text_sha256=None,
            ))
    return VerifyBundle(
        rendered_text="\n".join(lines),
        authored_text="\n".join(authored_lines),
        # The joined strings above stay for display/back-compat; V1 and V5 judge these
        # per-unit lists so a violation is always attributable to one finding.
        authored_units=list(authored_lines),
        rendered_units=list(lines),
        metrics=metrics,
        fact_store_values=[],
        evidence_claims=evidence,
        section_texts=section_texts,
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
            "source_sha256": filing.raw_sha256 or (
                hashlib.sha256(html.encode("utf-8")).hexdigest()
                if html is not None else None
            ),
            "is_amendment": bool(filing.is_amendment),
            "amends_accession": pp.amends_accession,
        }
        # --- metrics -------------------------------------------------------
        # Metrics run before research so the model can inspect deterministic results
        # through get_metric; the public stage names and persistence remain unchanged.
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
        data_quality = self.metrics_service.data_quality(
            filing.cik, as_of=as_of, form_type=filing.form_type
        )

        prior_sections: dict[str, dict] = {}
        filing_meta["has_prior_comparable"] = self.repo.has_prior_comparable_filing(
            filing.cik, pp.form_family, filing.filed_at
        )
        for section_key in sections:
            prior = self.repo.prior_comparable_section(
                filing.cik, pp.form_family, section_key, filing.filed_at
            )
            if prior is not None:
                prior_sections[section_key] = {
                    "accession_number": prior[0], "text": prior[1]
                }

        # --- P1: bounded tool-calling research -----------------------------
        linked_attempt = self.repo.latest_linked_p1_attempt(filing.accession_number)
        stored_p1 = linked_attempt[0] if linked_attempt is not None else None
        stored_trace = linked_attempt[1] if linked_attempt is not None else None
        if reusable("extract", stored_p1) and stored_trace is not None:
            p1_out = P1Output.model_validate_json(stored_p1.output_json)
            p1_aid = stored_p1.id
            trace = HarnessTrace.model_validate_json(stored_trace.output_json)
            trace_aid = stored_trace.id
            assert p1_aid is not None and trace_aid is not None
            reporter.completed(
                "extract",
                {"analysis_id": p1_aid, "resumed": True},
                message="Extracted (reused stored analysis)",
            )
        else:
            harness_result = run_stage(
                "extract",
                lambda: self.p1.run(
                    filing_meta=filing_meta,
                    sections=sections,
                    prior_sections=prior_sections,
                    metrics=metrics,
                    data_quality=data_quality,
                ),
                lambda result: {
                    "analysis_id": result.analysis_id,
                    "severity": result.output.classification.overall_severity,
                    "finding_count": len(result.output.findings),
                },
            )
            p1_out = harness_result.output
            p1_aid = harness_result.analysis_id
            trace_aid = harness_result.trace_analysis_id
            trace = harness_result.trace

        # --- verify: launch-output gate (V1/V4/V5) ---------------------------
        # A blocking failure withholds all LLM-derived output. There is no in-place
        # regeneration; a production retry is a fresh full attempt (pipeline/run.py).
        reporter.running("verify")
        try:
            bundle = assemble_verify_bundle(
                p1_out, metrics,
                section_texts_from_repo(self.repo, filing.accession_number),
                disclaimer=self.disclaimer,
            )
            core = run_all(bundle)

            # --- data-quality audit: V2 accounting identities ----------------
            # V2 validates source data, never authorizes an LLM finding.
            combined = list(core.results) + list(data_quality)
            blocking = any(
                check.verdict == "fail" and check.severity == "blocking"
                for check in combined
            )
            warns = any(check.verdict == "warn" for check in combined)
            report = VerificationReport(
                verdict="FAIL" if blocking else "PASS_WITH_WARNINGS" if warns else "PASS",
                results=combined,
            )
        except Exception:
            finalize_attempt(
                self.repo,
                trace=trace,
                trace_analysis_id=trace_aid,
                p1_analysis_id=p1_aid,
                p1=p1_out,
                sections=sections,
                report=None,
                processed_at=self._now_fn(),
            )
            reporter.failed("verify", "verification incomplete")
            raise

        finalize_attempt(
            self.repo,
            trace=trace,
            trace_analysis_id=trace_aid,
            p1_analysis_id=p1_aid,
            p1=p1_out,
            sections=sections,
            report=report,
            processed_at=self._now_fn(),
        )
        reporter.completed("verify", {"verdict": report.verdict, "checks": len(report.results)})

        return FilingAnalysis(
            accession_number=filing.accession_number, ticker=ticker, p1=p1_out,
            p1_analysis_id=p1_aid, trace_analysis_id=trace_aid, metrics=metrics,
            verification=report, withheld=report.verdict == "FAIL",
        )
