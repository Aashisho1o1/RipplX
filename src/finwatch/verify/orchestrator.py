"""Verifier orchestration — the §14 regeneration policy + persistence.

The verifier itself (checks.run_all, V1–V5) is Tier 1 and NEVER edits content. This
module is the policy the pipeline applies to its report: on a blocking FAIL,
regenerate the failing stage (≤ 2 retries); if it still fails, flag the item for
manual review. It also persists every CheckResult to ``verification_results`` and
provides small helpers for assembling the parts of a VerifyBundle that live in the DB.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from finwatch.core.types import SectorInfo
from finwatch.db.repositories import Repo, VerificationResult
from finwatch.verify.checks import (
    CheckResult,
    VerificationReport,
    VerifyBundle,
    run_all,
)
from finwatch.xbrl.normalize import FactStore

MANUAL_REVIEW_NOTICE = "⚠ manual review required — automated verification failed"

# (report, attempt_number) -> a regenerated bundle, or None to give up.
RegenerateFn = Callable[[VerificationReport, int], VerifyBundle | None]


@dataclass
class VerificationOutcome:
    report: VerificationReport
    regenerations: int          # regeneration attempts performed (0..max_retries)
    manual_review: bool         # still blocking-failing after retries

    def failures(self) -> list[CheckResult]:
        return self.report.failed()


def run_with_regeneration(
    bundle: VerifyBundle,
    regenerate: RegenerateFn,
    *,
    store: FactStore | None = None,
    sector: SectorInfo | None = None,
    max_retries: int = 2,
) -> VerificationOutcome:
    """Run the verifier; on blocking FAIL, regenerate + re-run up to ``max_retries``.

    Store/sector (the XBRL data) are held constant across retries — regeneration
    re-runs an LLM stage, not the numbers. Returns the final report and whether the
    item must be flagged for manual review.
    """
    report = run_all(bundle, store, sector)
    regenerations = 0
    while report.verdict == "FAIL" and regenerations < max_retries:
        new_bundle = regenerate(report, regenerations + 1)
        if new_bundle is None:
            break
        report = run_all(new_bundle, store, sector)
        regenerations += 1
    return VerificationOutcome(
        report=report,
        regenerations=regenerations,
        manual_review=report.verdict == "FAIL",
    )


def persist_report(
    repo: Repo, analysis_id: int, report: VerificationReport, *, created_at: str
) -> int:
    """Persist every CheckResult in the report to ``verification_results``."""
    rows = [
        VerificationResult(
            analysis_id=analysis_id, check_id=c.check_id, verdict=c.verdict,
            severity=c.severity, detail=c.detail or None, created_at=created_at,
        )
        for c in report.results
    ]
    return repo.insert_verification_results(rows)


def verify_and_store(
    bundle: VerifyBundle,
    repo: Repo,
    analysis_id: int,
    *,
    store: FactStore | None = None,
    sector: SectorInfo | None = None,
    created_at: str,
) -> VerificationReport:
    """Run the verifier once and persist the report (no regeneration)."""
    report = run_all(bundle, store, sector)
    persist_report(repo, analysis_id, report, created_at=created_at)
    return report


# -- VerifyBundle assembly helpers (parts sourced from the DB) ---------------
def fact_values_from_repo(repo: Repo, cik: str) -> list[float]:
    """Numeric XBRL leaves for V1's candidate pool (from the xbrl_facts table)."""
    return [f.value for f in repo.list_xbrl_facts(cik) if f.value is not None]


def section_texts_from_repo(repo: Repo, accession_number: str) -> dict[str, str]:
    """``{accession}:{section_key}`` → section text, for V4 citation checks."""
    return {
        f"{accession_number}:{s.section_key}": s.text
        for s in repo.list_filing_sections(accession_number)
    }
