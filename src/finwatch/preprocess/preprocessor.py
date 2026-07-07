"""P0 orchestrator: primary doc → canonical sections → persistence + risk diff.

Deterministic. Given a filing's raw primary document, it routes sections by form
family, persists them (keeping the FTS index in sync), links amendments to the
filing they correct, and computes the risk-factor diff against the prior
comparable filing. P1 then receives already-labelled sections.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finwatch.db.repositories import Filing, FilingSection, Repo
from finwatch.preprocess.diff import RiskFactorDiff, diff_risk_factors
from finwatch.preprocess.eightk import split_8k
from finwatch.preprocess.forms import base_form, form_family, is_amendment
from finwatch.preprocess.html import html_to_text
from finwatch.preprocess.sections import Section, split_10k, split_10q

_RISK_SECTION_KEY = {"10-K": "risk_factors", "10-Q": "risk_factor_changes"}
_SUPPORTED_FAMILIES = frozenset({"10-K", "10-Q", "8-K"})


@dataclass
class PreprocessResult:
    accession_number: str
    form_family: str
    sections: list[FilingSection] = field(default_factory=list)
    amends_accession: str | None = None
    risk_factor_diff: RiskFactorDiff | None = None


def route_sections(form_type: str, html: str) -> list[Section]:
    """Pure routing: form + raw doc → canonical sections (no persistence)."""
    doc = html_to_text(html)
    family = form_family(form_type)
    if family == "10-K":
        return split_10k(doc)
    if family == "10-Q":
        return split_10q(doc)
    if family == "8-K":
        return split_8k(doc)
    return []  # 20-F/6-K and others: no canonical routing yet


class Preprocessor:
    def __init__(
        self,
        repo: Repo,
        edgar=None,
        *,
        now_fn: Callable[[], str] | None = None,
    ) -> None:
        self.repo = repo
        self.edgar = edgar
        self._now_fn = now_fn or (lambda: datetime.now(UTC).isoformat())

    def preprocess_html(
        self,
        *,
        accession_number: str,
        cik: str,
        form_type: str,
        filed_at: str,
        period_of_report: str | None,
        html: str,
    ) -> PreprocessResult:
        family = form_family(form_type)
        sections = [
            self._to_row(accession_number, s) for s in route_sections(form_type, html)
        ]
        self.repo.replace_filing_sections(accession_number, sections)

        amends = None
        if is_amendment(form_type):
            amends = self.repo.find_amended_accession(
                cik, base_form(form_type), period_of_report, filed_at
            )
            self.repo.set_amends_accession(accession_number, amends)

        rf_diff = self._risk_factor_diff(cik, form_type, filed_at, sections)
        # A supported form that yields no sections is a routing failure (garbled or
        # exhibit-only document), not a clean pass — flag it so it is not silently
        # treated as analyzed. Unsupported families (20-F/6-K) legitimately route none.
        failed = family in _SUPPORTED_FAMILIES and not sections
        self.repo.set_filing_status(
            accession_number, "failed" if failed else "sectioned", self._now_fn()
        )
        return PreprocessResult(
            accession_number=accession_number, form_family=family, sections=sections,
            amends_accession=amends, risk_factor_diff=rf_diff,
        )

    def preprocess_filing(self, filing: Filing) -> PreprocessResult:
        """Fetch the primary doc via EDGAR (immutable → cached forever) and process."""
        if self.edgar is None:
            raise ValueError("preprocess_filing requires an EDGAR client")
        if not filing.primary_doc_url:
            raise ValueError(f"{filing.accession_number}: no primary_doc_url to fetch")
        cache_name = (
            f"filings/{filing.accession_number.replace('-', '')}_"
            f"{filing.primary_doc_url.rsplit('/', 1)[-1]}"
        )
        raw = self.edgar.fetch_primary_doc(filing.primary_doc_url, cache_name=cache_name)
        return self.preprocess_html(
            accession_number=filing.accession_number, cik=filing.cik,
            form_type=filing.form_type, filed_at=filing.filed_at,
            period_of_report=filing.period_of_report,
            html=raw.decode("utf-8", errors="replace"),
        )

    def load_result(self, filing: Filing) -> PreprocessResult:
        """Rebuild P0's result from persisted sections for a resumed downstream stage."""
        sections = self.repo.list_filing_sections(filing.accession_number)
        return PreprocessResult(
            accession_number=filing.accession_number,
            form_family=form_family(filing.form_type),
            sections=sections,
            amends_accession=filing.amends_accession,
            risk_factor_diff=self._risk_factor_diff(
                filing.cik, filing.form_type, filing.filed_at, sections
            ),
        )

    # -- internals ---------------------------------------------------------
    def _to_row(self, accession_number: str, s: Section) -> FilingSection:
        return FilingSection(
            accession_number=accession_number, section_key=s.section_key, title=s.title,
            char_start=s.char_start, char_end=s.char_end, html_element_id=s.element_id,
            is_furnished=int(s.is_furnished), text=s.text, text_sha256=s.text_sha256,
        )

    def _risk_factor_diff(
        self, cik: str, form_type: str, filed_at: str, sections: list[FilingSection]
    ) -> RiskFactorDiff | None:
        """Diff this filing's risk section against the PRIOR COMPARABLE filing — same base
        form + same canonical section key (a 10-K's `risk_factors` vs the prior 10-K's; a
        10-Q's `risk_factor_changes` vs the prior 10-Q's).

        Design note (F14): a 10-Q's Part II Item 1A is, by SEC rule, only the *material
        changes vs the latest 10-K*, so the section is ALREADY a delta and P1 treats any
        content in it as inherently notable (§11 T2). Diffing that small delta against the
        full 10-K `risk_factors` would produce a large, low-signal diff (the whole 10-K
        reading as "removed", the whole delta as "added"); diffing delta-vs-prior-delta is
        the more informative comparison. So comparing like-for-like base forms is deliberate,
        not an oversight — a 10-Q intentionally does not diff against the 10-K here."""
        key = _RISK_SECTION_KEY.get(form_family(form_type))
        if key is None:
            return None
        current = next((s for s in sections if s.section_key == key), None)
        if current is None:
            return None
        prior = self.repo.prior_comparable_section(cik, base_form(form_type), key, filed_at)
        if prior is None:
            return None
        return diff_risk_factors(prior[1], current.text)
