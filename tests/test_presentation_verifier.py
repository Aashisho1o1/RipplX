import hashlib

import pytest

from finwatch.db.repositories import FilingSection
from finwatch.llm.schemas import Finding, FindingEvidence
from finwatch.presentation.models import EvidenceView, FilingDigestEntry, FindingView
from finwatch.verify.presentation import verify_filing_entry

_ACCESSION = "0000000001-26-000001"
_URL = (
    "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/"
    "0000000001-26-000001-index.htm"
)
_QUANTITY_OR_ADVICE_BYPASSES = [
    "Revenue increased a dozen basis points",
    "Demand increased by a score",
    "Revenue increased by a quarter",
    "Revenue changed by a fraction",
    "Investors should avoid the shares",
    "Exit the position",
    "Reduce exposure to the shares",
    "Stay away from the stock",
]


def _fixture() -> tuple[FilingDigestEntry, dict[str, FilingSection]]:
    text = "Revenue was $1.2 billion and liquidity remained stable."
    quote = "$1.2 billion"
    start = text.index(quote)
    digest = hashlib.sha256(text.encode()).hexdigest()
    section = FilingSection(
        accession_number=_ACCESSION,
        section_key="mdna",
        char_start=0,
        char_end=len(text),
        text=text,
        text_sha256=digest,
    )
    evidence = EvidenceView(
        claim_id="evidence-a",
        accession=_ACCESSION,
        section_key="mdna",
        char_start=start,
        char_end=start + len(quote),
        quote=quote,
        section_sha256=digest,
        edgar_url=_URL,
    )
    entry = FilingDigestEntry(
        accession=_ACCESSION,
        ticker="TEST",
        form="10-Q",
        filed="2026-07-09",
        edgar_url=_URL,
        findings=[
            FindingView(
                finding_id="finding-a",
                headline="Liquidity remained stable",
                severity="MEDIUM",
                evidence=[evidence],
            )
        ],
    )
    return entry, {"mdna": section}


def test_exact_quote_with_number_passes():
    entry, sections = _fixture()
    assert verify_filing_entry(entry, sections) == []


@pytest.mark.parametrize(
    "headline",
    [
        "Revenue fell 5 percent",
        "Revenue fell fifty percent",
        "Revenue fell −5%",
        "Exposure reached 1e9",
        "Buy the shares",
        "The target price is higher",
        "We estimate a fair value for the shares",
        "Guaranteed upside",
        "Margins moved ½ a point",
        "Margins moved by 25 bps",
        *_QUANTITY_OR_ADVICE_BYPASSES,
    ],
)
def test_authored_numeric_or_advice_headline_fails(headline: str):
    entry, sections = _fixture()
    finding = entry.findings[0].model_copy(update={"headline": headline})
    mutated = entry.model_copy(update={"findings": [finding]})
    assert verify_filing_entry(mutated, sections)


@pytest.mark.parametrize("headline", _QUANTITY_OR_ADVICE_BYPASSES)
def test_p1_schema_leaves_headline_policy_to_the_per_finding_compiler(headline: str):
    finding = Finding(
        finding_id="f1",
        headline=headline,
        severity="medium",
        evidence=[
            FindingEvidence(
                accession_number=_ACCESSION,
                form_type="10-Q",
                section_key="mdna",
                char_start=0,
                char_end=1,
                snippet="x",
            )
        ],
    )
    assert finding.headline == headline


@pytest.mark.parametrize(
    "headline",
    ["Honeymoon demand normalized", "Obviously different controls were disclosed"],
)
def test_final_dto_allows_forbidden_substrings_that_are_not_tokens(headline: str):
    entry, sections = _fixture()
    finding = entry.findings[0].model_copy(update={"headline": headline})
    assert verify_filing_entry(entry.model_copy(update={"findings": [finding]}), sections) == []


def test_wrong_exact_offset_fails():
    entry, sections = _fixture()
    evidence = entry.findings[0].evidence[0]
    evidence = evidence.model_copy(
        update={"char_start": evidence.char_start + 1, "char_end": evidence.char_end + 1}
    )
    finding = entry.findings[0].model_copy(update={"evidence": [evidence]})
    mutated = entry.model_copy(update={"findings": [finding]})
    assert any("not exact" in error for error in verify_filing_entry(mutated, sections))


def test_wrong_hash_fails():
    entry, sections = _fixture()
    evidence = entry.findings[0].evidence[0].model_copy(update={"section_sha256": "0" * 64})
    finding = entry.findings[0].model_copy(update={"evidence": [evidence]})
    mutated = entry.model_copy(update={"findings": [finding]})
    assert any("hash mismatch" in error for error in verify_filing_entry(mutated, sections))


def test_non_sec_citation_and_withheld_content_fail():
    entry, sections = _fixture()
    mutated = entry.model_copy(
        update={"edgar_url": "https://attacker.example/filing", "withheld": True}
    )
    errors = verify_filing_entry(mutated, sections)
    assert "withheld entry contains findings" in errors
    assert "citation URL is not an HTTPS SEC URL" in errors
