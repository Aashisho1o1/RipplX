"""Canonical persisted filing projection shared by Markdown and web presenters."""

from __future__ import annotations

from dataclasses import dataclass, field

from finwatch.db.repositories import Analysis, Company, Filing, Repo
from finwatch.llm.harness import HarnessTrace
from finwatch.llm.schemas import P1Output

_REQUIRED_PUBLICATION_CHECKS = frozenset({"V1", "V4", "V5"})
GATE_WITHHELD_REASON = (
    "LLM-derived analysis withheld because deterministic verification did not pass."
)
PIPELINE_FAILED_REASON = (
    "Analysis did not complete for this filing, so nothing was published. "
    "No deterministic verification result was recorded for this attempt."
)


@dataclass
class FilingProjection:
    filing: Filing
    company: Company | None
    p1: P1Output | None
    analysis_present: bool
    llm_output_allowed: bool
    withheld: bool
    withheld_kind: str | None = None
    withheld_reason: str | None = None
    data_quality: list[tuple[str, str]] = field(default_factory=list)
    p1_analysis: Analysis | None = None
    trace_analysis: Analysis | None = None
    trace: HarnessTrace | None = None

    @property
    def ticker(self) -> str:
        return self.company.ticker if self.company else self.filing.cik

    @property
    def severity(self) -> str:
        if self.p1:
            return self.p1.classification.overall_severity
        return "withheld" if self.analysis_present else "unanalyzed"

    @property
    def is_critical(self) -> bool:
        return bool(self.p1) and self.severity in {"critical", "high"}


def load_filing_projection(repo: Repo, filing: Filing) -> FilingProjection:
    latest_trace = repo.latest_analysis(filing.accession_number, "P1_TRACE")
    analysis_present = (
        latest_trace is not None
        or repo.latest_analysis(filing.accession_number, "P1") is not None
    )
    linked = repo.latest_linked_p1_attempt(filing.accession_number)
    p1a = linked[0] if linked is not None else None
    trace_row = linked[1] if linked is not None else None
    trace = None
    if trace_row is not None:
        try:
            candidate = HarnessTrace.model_validate_json(trace_row.output_json)
        except Exception:  # noqa: BLE001 - malformed state fails closed
            candidate = None
        if (
            candidate is not None
            and candidate.trace_analysis_id == trace_row.id
            and candidate.publication_outcome is not None
            and candidate.terminal_reason is not None
            and candidate.filing_snapshot.get("accession") == filing.accession_number
            and candidate.filing_snapshot.get("form") == filing.form_type
        ):
            trace = candidate
    results = repo.list_verification_results(p1a.id) if p1a else []
    # Strict publication gate: each required check must have exactly one persisted row
    # and that row must PASS. With server-side offset anchoring, production V4 is
    # deterministic — it passes on unique verbatim evidence or blocking-fails on
    # missing/ambiguous/corrupted evidence — so there is no legitimate "warning" state
    # on a required check to tolerate. V2 data-quality warnings are non-blocking and are
    # NOT required publication checks, so they never gate here.
    def _passed(check_id: str) -> bool:
        rows = [row for row in results if row.check_id == check_id]
        return len(rows) == 1 and rows[0].verdict == "pass"

    required_passed = all(_passed(check_id) for check_id in _REQUIRED_PUBLICATION_CHECKS)
    blocking = any(
        row.verdict == "fail" and row.severity == "blocking" for row in results
    )
    llm_output_allowed = bool(
        p1a
        and trace
        and filing.status == "verified"
        and trace.publication_outcome in {"published", "partial", "metrics_only"}
        and trace.verification_verdict in {"PASS", "PASS_WITH_WARNINGS"}
        and required_passed
        and not blocking
    )
    p1 = None
    if llm_output_allowed:
        try:
            p1 = P1Output.model_validate_json(p1a.output_json)
        except Exception:  # noqa: BLE001 - corrupt persisted output must fail closed
            llm_output_allowed = False
            p1 = None
    pipeline_failed = filing.status == "failed"
    withheld = (
        pipeline_failed
        or filing.status == "analyzed"
        or bool(analysis_present and not llm_output_allowed)
    )
    withheld_kind = (
        "pipeline_failed" if pipeline_failed else "gate" if withheld else None
    )
    withheld_reason = (
        PIPELINE_FAILED_REASON
        if withheld_kind == "pipeline_failed"
        else GATE_WITHHELD_REASON
        if withheld_kind == "gate"
        else None
    )
    data_quality: list[tuple[str, str]] = []
    if p1a:
        data_quality = [
            (row.check_id, row.detail or "") for row in results if row.verdict == "warn"
        ]
    return FilingProjection(
        filing=filing,
        company=repo.get_company(filing.cik),
        p1=p1,
        analysis_present=analysis_present,
        llm_output_allowed=llm_output_allowed,
        withheld=withheld,
        withheld_kind=withheld_kind,
        withheld_reason=withheld_reason,
        data_quality=data_quality,
        p1_analysis=p1a,
        trace_analysis=trace_row,
        trace=trace,
    )

def in_window(filing: Filing, since: str | None, until: str | None) -> bool:
    filed = (filing.filed_at or "")[:10]
    return not ((since and filed < since[:10]) or (until and filed > until[:10]))
