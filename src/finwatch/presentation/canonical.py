"""Build the one evidence-backed filing projection used by every launch surface."""

from __future__ import annotations

import hashlib
import re

from finwatch.db.repositories import Filing, FilingSection, Repo
from finwatch.llm.schemas import FindingEvidence
from finwatch.presentation.models import EvidenceView, FilingDigestEntry, FindingView
from finwatch.presentation.projection import FilingProjection
from finwatch.verify.presentation import verify_filing_entry

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
_WITHHELD = (
    "LLM-derived analysis withheld because its displayed findings could not be verified exactly."
)

def _date(value: str | None) -> str:
    return (value or "")[:10]


def _edgar_url(filing: Filing) -> tuple[str, bool]:
    """Construct a citation from trusted filing identity, never a stored arbitrary URL."""
    if not filing.cik.isdigit() or not _ACCESSION.fullmatch(filing.accession_number):
        return "https://www.sec.gov/edgar/search/", False
    cik = str(int(filing.cik))
    accession = filing.accession_number
    compact = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{compact}/"
        f"{accession}-index.htm",
        True,
    )


def _base_entry(view: FilingProjection, *, withheld: bool | None = None) -> FilingDigestEntry:
    url, _ = _edgar_url(view.filing)
    return FilingDigestEntry(
        accession=view.filing.accession_number,
        ticker=view.ticker,
        form=view.filing.form_type,
        filed=_date(view.filing.filed_at),
        edgar_url=url,
        withheld=view.withheld if withheld is None else withheld,
        withheld_reason=view.withheld_reason,
    )


def _withhold(view: FilingProjection) -> FilingDigestEntry:
    entry = _base_entry(view, withheld=True)
    return entry.model_copy(update={"findings": [], "withheld_reason": _WITHHELD})


def _section_map(sections: list[FilingSection]) -> tuple[dict[str, FilingSection], set[str]]:
    by_key: dict[str, FilingSection] = {}
    duplicates: set[str] = set()
    for section in sections:
        if section.section_key in by_key:
            duplicates.add(section.section_key)
        else:
            by_key[section.section_key] = section
    return by_key, duplicates


def _exact_evidence(
    view: FilingProjection,
    source_index: int,
    raw_evidence: list[FindingEvidence],
    sections: dict[str, FilingSection],
    duplicate_section_keys: set[str],
    edgar_url: str,
) -> tuple[list[EvidenceView], list[str]]:
    evidence: list[EvidenceView] = []
    errors: list[str] = []
    for evidence_index, provenance in enumerate(raw_evidence):
        evidence_id = f"finding-{source_index + 1}-evidence-{evidence_index + 1}"
        if provenance.accession_number != view.filing.accession_number:
            errors.append(f"{evidence_id}: untrusted accession")
            continue
        if provenance.form_type != view.filing.form_type:
            errors.append(f"{evidence_id}: form mismatch")
            continue
        if provenance.section_key in duplicate_section_keys:
            errors.append(f"{evidence_id}: ambiguous section key")
            continue
        section = sections.get(provenance.section_key)
        if section is None:
            errors.append(f"{evidence_id}: section missing")
            continue
        start, end = provenance.char_start, provenance.char_end
        snippet = provenance.snippet
        if not snippet or len(snippet) > 2_000:
            errors.append(f"{evidence_id}: quote is empty or too long")
            continue
        if not (0 <= start < end <= len(section.text)):
            errors.append(f"{evidence_id}: declared span is out of bounds")
            continue
        if section.text[start:end] != snippet:
            errors.append(f"{evidence_id}: declared span is not the exact quote")
            continue
        actual_hash = hashlib.sha256(section.text.encode()).hexdigest()
        evidence.append(
            EvidenceView(
                claim_id=evidence_id,
                accession=view.filing.accession_number,
                section_key=section.section_key,
                char_start=start,
                char_end=end,
                quote=snippet,
                section_sha256=actual_hash,
                edgar_url=edgar_url,
            )
        )
    if not evidence:
        errors.append("no direct evidence")
    return evidence[:3], errors


def build_filing_entry(repo: Repo, view: FilingProjection) -> FilingDigestEntry:
    """Return a fail-closed, maximum-three-finding view for one filing."""
    entry = _base_entry(view)
    _, identity_valid = _edgar_url(view.filing)
    if not identity_valid:
        return _withhold(view)
    if not view.analysis_present:
        return entry
    if not view.llm_output_allowed or not view.p1:
        return _withhold(view)

    sections, duplicates = _section_map(
        repo.list_filing_sections(view.filing.accession_number)
    )
    findings: list[FindingView] = []
    seen_evidence: set[tuple[tuple[str, int, int], ...]] = set()
    for source_index, candidate in enumerate(view.p1.findings):
        evidence, errors = _exact_evidence(
            view,
            source_index,
            candidate.evidence,
            sections,
            duplicates,
            entry.edgar_url,
        )
        if errors:
            continue
        evidence_key = tuple(
            sorted((row.section_key, row.char_start, row.char_end) for row in evidence)
        )
        if evidence_key in seen_evidence:
            # Defence in depth only. verify/compiler.py drops duplicate-evidence
            # findings before the attempt snapshot is frozen, so a compiler-approved
            # attempt cannot reach here with duplicates; this guard now catches only
            # persisted state that bypassed the compiler.
            continue
        seen_evidence.add(evidence_key)
        try:
            findings.append(
                FindingView(
                    finding_id=candidate.finding_id,
                    headline=candidate.headline,
                    severity=candidate.severity.upper(),
                    evidence=evidence,
                )
            )
        except Exception:  # noqa: BLE001 - invalid LLM-authored display text fails closed
            continue

    findings.sort(key=lambda row: (_SEVERITY_RANK[row.severity], row.finding_id))
    entry = entry.model_copy(update={"findings": findings[:3]})
    if verify_filing_entry(entry, sections):
        return _withhold(view)
    return entry
