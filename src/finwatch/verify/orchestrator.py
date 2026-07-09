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
    check_v2_identities,
    run_all,
)
from finwatch.xbrl.normalize import FactStore

MANUAL_REVIEW_NOTICE = "⚠ manual review required — automated verification failed"

# Forms whose latest balance-sheet instant is a fiscal-year-end, so the V2b cash tie-out
# (which compares a fiscal-YEAR change) is period-aligned. On a 10-Q the latest instant is a
# quarter-end and V2b would systematically false-fail, so it is skipped there (§14 V2b).
_ANNUAL_FORMS = frozenset({"10-K", "10-KSB", "10-K405", "20-F", "40-F"})


def data_quality_report(
    store: FactStore, sector: SectorInfo, *, form_type: str
) -> list[CheckResult]:
    """V2 accounting identities run as a DATA-QUALITY audit (separate from the LLM
    regeneration gate — regenerating an LLM stage can never repair the XBRL numbers).

    V2a (A = L + E) and V2c (Rev ≥ GP ≥ OpInc) run on every filing. V2b (BS-cash Δ vs CF
    cash change) compares a fiscal-year change, so it only applies to annual filings; on a
    non-annual filing a blocking V2b result is reclassified skipped_not_applicable.

    V2a compares three concepts, each resolved to its own latest instant, so it is only
    meaningful when all three land on the SAME period-end; when they don't (e.g. a concept
    was last reported in a different period), a blocking V2a is reclassified
    skipped_not_applicable rather than false-failing across mismatched dates.

    NON-BLOCKING by design: any remaining V2 failure is surfaced as a data-quality WARNING,
    never a blocking failure. V2 validates the XBRL DATA — which re-running an LLM stage can
    never repair — and the raw identities false-fail on legitimate accounting structures too
    often to quarantine a whole filing on them: A=L+E uses parent-only StockholdersEquity so
    it breaks for any consolidated issuer with noncontrolling interest, and the cash tie-out
    compares unrestricted balance-sheet cash against the restricted-cash-inclusive cash-flow
    reconciliation (ASU 2016-18). So V2 informs the digest (open questions) without
    suppressing the independently-verified qualitative analysis (fewer/sharper alerts)."""
    annual = (form_type or "").upper().split("/")[0] in _ANNUAL_FORMS
    v2a_aligned = _balance_sheet_aligned(store)
    out: list[CheckResult] = []
    for r in check_v2_identities(store, sector):
        if r.check_id == "V2a" and not v2a_aligned:
            # A=L+E is not checkable when the three concepts resolve to different
            # period-ends — skip regardless of whether it coincidentally ties.
            out.append(CheckResult(
                check_id="V2a", verdict="skipped_not_applicable", severity="info",
                detail="assets/liabilities/equity resolved to different period-ends; "
                       "A=L+E is not checkable across mismatched periods"))
        elif r.check_id == "V2b" and not annual and r.verdict == "fail":
            out.append(CheckResult(
                check_id="V2b", verdict="skipped_not_applicable", severity="info",
                detail="cash tie-out compares the fiscal-year change; not applicable on a "
                       "non-annual filing (latest cash instant is a quarter-end)"))
        elif r.verdict == "fail":
            # data-quality signal, not a blocking gate (see docstring)
            out.append(CheckResult(check_id=r.check_id, verdict="warn", severity="warning",
                                   detail=r.detail))
        else:
            out.append(r)
    return out


def _balance_sheet_aligned(store: FactStore) -> bool:
    """True when total_assets, total_liabilities, and equity all resolve to the same
    period-end (the precondition for the A = L + E identity to be checkable)."""
    keys = set()
    for concept in ("total_assets", "total_liabilities", "equity"):
        r = store.latest_instant(concept)
        if r is None:
            return False
        keys.add(r.fact.end)          # instant date is the balance-sheet period-end
    return len(keys) == 1

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
    """Persist every CheckResult in the report to ``verification_results``.

    A re-verify of the same analysis REPLACES the prior rows (not appends): stale
    blocking FAILs are cleared first so ``manual_review`` (derived from any-blocking-
    fail over all rows) reflects only the latest run, and rows don't accumulate on
    every retry.
    """
    repo.clear_verification_results(analysis_id)
    rows = [
        VerificationResult(
            analysis_id=analysis_id, check_id=c.check_id, verdict=c.verdict,
            severity=c.severity, detail=c.detail or None, created_at=created_at,
        )
        for c in report.results
    ]
    return repo.insert_verification_results(rows)




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
