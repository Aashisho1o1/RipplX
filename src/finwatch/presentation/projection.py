"""Canonical persisted filing projection shared by Markdown and web presenters."""

from __future__ import annotations

from dataclasses import dataclass, field

from finwatch.db.repositories import Company, Filing, Holding, Repo
from finwatch.llm.schemas import P1Output

_REQUIRED_PUBLICATION_CHECKS = frozenset({"V1", "V4", "V5"})


@dataclass
class FilingProjection:
    filing: Filing
    company: Company | None
    holding: Holding | None
    p1: P1Output | None
    analysis_present: bool
    llm_output_allowed: bool
    manual_review: bool
    withheld_reason: str | None = None
    data_quality: list[tuple[str, str]] = field(default_factory=list)

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
    p1a = repo.latest_analysis(filing.accession_number, "P1")
    analysis_present = p1a is not None
    results = repo.list_verification_results(p1a.id) if p1a else []
    by_id = {row.check_id: row for row in results}
    required_passed = all(
        check_id in by_id and by_id[check_id].verdict == "pass"
        for check_id in _REQUIRED_PUBLICATION_CHECKS
    )
    blocking = any(
        row.verdict == "fail" and row.severity == "blocking" for row in results
    )
    llm_output_allowed = bool(
        p1a and filing.status == "verified" and required_passed and not blocking
    )
    p1 = None
    if llm_output_allowed:
        try:
            p1 = P1Output.model_validate_json(p1a.output_json)
        except Exception:  # noqa: BLE001 - corrupt persisted output must fail closed
            llm_output_allowed = False
            p1 = None
        else:
            # Existing verification rows may predate the incomplete-extraction gate.
            # Reapply the publication invariant so an old low-confidence or gapped
            # artifact cannot appear as a reassuring "routine" filing after upgrade.
            if p1.extraction_confidence == "low" or p1.gaps:
                llm_output_allowed = False
                p1 = None
    manual_review = filing.status == "failed" or bool(analysis_present and not llm_output_allowed)
    withheld_reason = (
        "LLM-derived analysis withheld because deterministic verification did not pass."
        if manual_review and analysis_present
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
        holding=repo.get_holding_by_cik(filing.cik),
        p1=p1,
        analysis_present=analysis_present,
        llm_output_allowed=llm_output_allowed,
        manual_review=manual_review,
        withheld_reason=withheld_reason,
        data_quality=data_quality,
    )

def in_window(filing: Filing, since: str | None, until: str | None) -> bool:
    filed = (filing.filed_at or "")[:10]
    return not ((since and filed < since[:10]) or (until and filed > until[:10]))
