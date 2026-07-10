"""Canonical persisted filing projection shared by Markdown and web presenters."""

from __future__ import annotations

from dataclasses import dataclass, field

from finwatch.db.repositories import Company, Filing, Holding, Repo
from finwatch.llm.schemas import Claim, P1Output, P2Output


@dataclass
class FilingProjection:
    filing: Filing
    company: Company | None
    holding: Holding | None
    p1: P1Output | None
    p2: P2Output | None
    claims: dict[str, Claim]
    manual_review: bool
    data_quality: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ticker(self) -> str:
        return self.company.ticker if self.company else self.filing.cik

    @property
    def severity(self) -> str:
        return self.p1.classification.overall_severity if self.p1 else "unanalyzed"

    @property
    def is_critical(self) -> bool:
        return bool(self.p1) and (self.severity in {"critical", "high"} or bool(self.p1.red_flags))


def load_filing_projection(repo: Repo, filing: Filing) -> FilingProjection:
    p1a = repo.latest_analysis(filing.accession_number, "P1")
    p2a = repo.latest_analysis(filing.accession_number, "P2")
    p1 = P1Output.model_validate_json(p1a.output_json) if p1a else None
    p2 = P2Output.model_validate_json(p2a.output_json) if p2a else None
    claims = {claim.claim_id: claim for claim in p1.claims} if p1 else {}
    manual_review = filing.status == "failed"
    data_quality: list[tuple[str, str]] = []
    if p1a:
        results = repo.list_verification_results(p1a.id)
        manual_review = manual_review or any(
            row.verdict == "fail" and row.severity == "blocking" for row in results
        )
        data_quality = [
            (row.check_id, row.detail or "") for row in results if row.verdict == "warn"
        ]
    return FilingProjection(
        filing=filing,
        company=repo.get_company(filing.cik),
        holding=repo.get_holding_by_cik(filing.cik),
        p1=p1,
        p2=p2,
        claims=claims,
        manual_review=manual_review,
        data_quality=data_quality,
    )


def evidence_snippet(view: FilingProjection, claim_ids: list[str]) -> str | None:
    for claim_id in claim_ids:
        claim = view.claims.get(claim_id)
        if claim and claim.claim_type == "evidence" and claim.provenance:
            if claim.provenance.snippet:
                return claim.provenance.snippet
    return None


def has_impact(view: FilingProjection) -> bool:
    return view.p2 is not None and any(
        record.impact_class != "no_impact" for record in view.p2.records_affected
    )


def in_window(filing: Filing, since: str | None, until: str | None) -> bool:
    filed = (filing.filed_at or "")[:10]
    return not ((since and filed < since[:10]) or (until and filed > until[:10]))
