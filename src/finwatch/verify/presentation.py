"""Verification of the exact evidence-backed filing DTO emitted to users."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from finwatch.core.text_policy import contains_authored_quantity, contains_trade_instruction
from finwatch.core.types import FORBIDDEN_VOCABULARY
from finwatch.db.repositories import FilingSection
from finwatch.presentation.models import FilingDigestEntry

_PRICE_TARGET = re.compile(
    r"(price\s+target|target\s+price|will\s+(reach|hit)|"
    r"\$\d+(\.\d+)?\s*(PT\b|target\b|price\s+target))",
    re.IGNORECASE,
)


def verify_filing_entry(
    entry: FilingDigestEntry,
    sections: dict[str, FilingSection],
) -> list[str]:
    """Return deterministic publication errors for one exact browser/Markdown DTO."""
    errors: list[str] = []
    if entry.withheld and entry.findings:
        errors.append("withheld entry contains findings")
    if len(entry.findings) > 3:
        errors.append("more than three findings")

    parsed_url = urlsplit(entry.edgar_url)
    if parsed_url.scheme != "https" or parsed_url.hostname not in {"sec.gov", "www.sec.gov"}:
        errors.append("citation URL is not an HTTPS SEC URL")

    finding_ids: set[str] = set()
    for finding in entry.findings:
        if finding.finding_id in finding_ids:
            errors.append(f"duplicate finding id {finding.finding_id}")
        finding_ids.add(finding.finding_id)
        headline = finding.headline.strip()
        if not headline:
            errors.append(f"{finding.finding_id}: empty headline")
        if contains_authored_quantity(headline):
            errors.append(f"{finding.finding_id}: authored headline contains a number")
        lowered = headline.lower()
        if any(word in lowered for word in FORBIDDEN_VOCABULARY):
            errors.append(f"{finding.finding_id}: forbidden vocabulary")
        if contains_trade_instruction(headline) or _PRICE_TARGET.search(headline):
            errors.append(f"{finding.finding_id}: trade or price-target language")
        if not finding.evidence:
            errors.append(f"{finding.finding_id}: no direct evidence")

        evidence_ids: set[str] = set()
        for evidence in finding.evidence:
            if evidence.claim_id in evidence_ids:
                errors.append(f"{finding.finding_id}: duplicate evidence {evidence.claim_id}")
            evidence_ids.add(evidence.claim_id)
            if evidence.accession != entry.accession:
                errors.append(f"{evidence.claim_id}: accession mismatch")
            if evidence.edgar_url != entry.edgar_url:
                errors.append(f"{evidence.claim_id}: citation URL mismatch")
            section = sections.get(evidence.section_key)
            if section is None:
                errors.append(f"{evidence.claim_id}: section missing")
                continue
            actual_hash = hashlib.sha256(section.text.encode()).hexdigest()
            if section.text_sha256 != actual_hash or evidence.section_sha256 != actual_hash:
                errors.append(f"{evidence.claim_id}: section hash mismatch")
            if not (0 <= evidence.char_start < evidence.char_end <= len(section.text)):
                errors.append(f"{evidence.claim_id}: offsets out of bounds")
                continue
            if section.text[evidence.char_start:evidence.char_end] != evidence.quote:
                errors.append(f"{evidence.claim_id}: quote is not exact at declared offsets")
    return errors
