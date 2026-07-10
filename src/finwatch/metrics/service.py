"""Launch metrics orchestration — compute and persist the six starter metrics.

This service supplies only a point-in-time FactStore and issuer sector. Price, position,
valuation, target-weight, and portfolio inputs are deliberately absent from the launch path.
Every MetricResult is persisted verbatim (`model_dump_json`) to the `computations`
table (SYSTEM_DESIGN §4.3).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from finwatch.core.types import SectorInfo, sector_from_sic
from finwatch.db.repositories import Computation, Repo
from finwatch.metrics.envelope import MetricsBundle
from finwatch.metrics.formulas import compute_starter
from finwatch.verify.checks import CheckResult
from finwatch.verify.orchestrator import data_quality_report
from finwatch.xbrl.normalize import FactStore

CompanyFactsProvider = Callable[[str], dict]


def build_sector(sic_code: str | None) -> SectorInfo:
    """Canonical sector descriptor from a SIC (carries the SIC for Altman's
    manufacturer test)."""
    return sector_from_sic(sic_code)


def as_of_facts(companyfacts: dict, as_of: str | None) -> dict:
    """Return a companyfacts copy containing only facts FILED on or before ``as_of`` (F4).

    Without this, a historically-dated analysis (backfill / eval replay / the shadow-log
    track record) silently consumes facts from FUTURE filings and restatements — the
    accessors always pick the newest fact. Filing dates and ``as_of`` are compared on their
    ``YYYY-MM-DD`` prefix; entries without a ``filed`` date (essentially never, in SEC
    companyfacts) are kept. Also makes amendment supersession point-in-time."""
    cutoff = (as_of or "")[:10]
    facts = companyfacts.get("facts") if isinstance(companyfacts, dict) else None
    if not cutoff or not isinstance(facts, dict):
        return companyfacts
    new_facts: dict = {}
    for taxonomy, tags in facts.items():
        new_tags: dict = {}
        for tag, body in tags.items():
            if not isinstance(body, dict):
                new_tags[tag] = body
                continue
            new_units: dict = {}
            for unit, entries in body.get("units", {}).items():
                kept = [e for e in entries
                        if not isinstance(e, dict)
                        or e.get("filed") is None
                        or str(e["filed"])[:10] <= cutoff]
                if kept:
                    new_units[unit] = kept
            new_tags[tag] = {**body, "units": new_units}
        new_facts[taxonomy] = new_tags
    return {**companyfacts, "facts": new_facts}


class MetricsService:
    def __init__(
        self,
        repo: Repo,
        companyfacts_provider: CompanyFactsProvider,
        *,
        now_fn: Callable[[], str] | None = None,
    ) -> None:
        self.repo = repo
        self.companyfacts_provider = companyfacts_provider
        self._now_fn = now_fn or (lambda: datetime.now(UTC).isoformat())

    def _store_and_sector(self, cik: str, as_of: str):
        company = self.repo.get_company(cik)
        if company is None:
            raise ValueError(f"unknown company: {cik}")
        # F4: point-in-time — never let a historically-dated run see future filings.
        store = FactStore.from_companyfacts(
            as_of_facts(self.companyfacts_provider(cik), as_of))
        return store, build_sector(company.sic_code), company

    def compute(self, cik: str, *, as_of: str) -> MetricsBundle:
        store, sector, _company = self._store_and_sector(cik, as_of)
        return compute_starter(store, sector, as_of=as_of)

    def data_quality(self, cik: str, *, as_of: str, form_type: str) -> list[CheckResult]:
        """V2 accounting-identity audit for a filing (F10) — separate from the LLM gate."""
        store, sector, _ = self._store_and_sector(cik, as_of)
        return data_quality_report(store, sector, form_type=form_type)

    def compute_and_store(self, cik: str, *, as_of: str) -> tuple[MetricsBundle, int]:
        bundle = self.compute(cik, as_of=as_of)
        company = self.repo.get_company(cik)
        assert company is not None  # compute() already guarded
        return bundle, self.persist(company.ticker, bundle, as_of)

    def persist(self, ticker: str, bundle: MetricsBundle, as_of: str) -> int:
        created = self._now_fn()
        args = json.dumps({"ticker": ticker, "as_of": as_of})
        rows = [
            Computation(
                ticker=ticker, tool=r.metric, args_json=args,
                result_json=r.model_dump_json(), status=r.status.value,
                formula_version=r.formula_version, as_of=r.as_of, created_at=created,
            )
            for r in bundle.all_results()
        ]
        return self.repo.insert_computations(rows)
