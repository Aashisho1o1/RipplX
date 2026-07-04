"""Metrics orchestration — builds compute_all's inputs from the DB and persists results.

`compute_all` (formulas.py, Tier 1) is the only metrics entry point; this service is
the thin adapter that supplies it: SectorInfo from the stored company, a FactStore
from companyfacts, the owned Holding + portfolio market value, and a PriceProvider.
Every MetricResult is persisted verbatim (`model_dump_json`) to the `computations`
table (SYSTEM_DESIGN §4.3).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from finwatch.core.types import SectorInfo, sector_from_sic
from finwatch.db.repositories import Computation, Holding, Repo
from finwatch.metrics.envelope import MetricsBundle
from finwatch.metrics.formulas import Holding as MetricHolding
from finwatch.metrics.formulas import PriceProvider, compute_all
from finwatch.xbrl.normalize import FactStore

CompanyFactsProvider = Callable[[str], dict]


def build_sector(sic_code: str | None) -> SectorInfo:
    """Canonical sector descriptor from a SIC (carries the SIC for Altman's
    manufacturer test)."""
    return sector_from_sic(sic_code)


def _to_metric_holding(h: Holding) -> MetricHolding:
    return MetricHolding(
        ticker=h.ticker, owned=bool(h.owned), shares=h.shares, cost_basis=h.cost_basis,
        target_weight_pct=h.target_weight_pct, thesis=h.thesis, horizon=h.horizon,
    )


class MetricsService:
    def __init__(
        self,
        repo: Repo,
        price_provider: PriceProvider | None,
        companyfacts_provider: CompanyFactsProvider,
        *,
        now_fn: Callable[[], str] | None = None,
    ) -> None:
        self.repo = repo
        self.price_provider = price_provider
        self.companyfacts_provider = companyfacts_provider
        self._now_fn = now_fn or (lambda: datetime.now(UTC).isoformat())

    def compute(self, cik: str, *, as_of: str) -> MetricsBundle:
        company = self.repo.get_company(cik)
        if company is None:
            raise ValueError(f"unknown company: {cik}")
        store = FactStore.from_companyfacts(self.companyfacts_provider(cik))
        sector = build_sector(company.sic_code)

        holding_row = self.repo.get_holding_by_cik(cik)
        holding = None
        portfolio_mv = None
        if holding_row is not None and holding_row.owned:
            holding = _to_metric_holding(holding_row)
            portfolio_mv = self._portfolio_market_value(as_of)

        return compute_all(
            store, sector, ticker=company.ticker, price_provider=self.price_provider,
            as_of=as_of, holding=holding, portfolio_market_value=portfolio_mv,
        )

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

    def _portfolio_market_value(self, as_of: str) -> float | None:
        if self.price_provider is None:
            return None
        total = 0.0
        any_priced = False
        for h in self.repo.list_holdings(owned=True):
            if h.shares is None:
                continue
            px = self.price_provider.close_on_or_before(h.ticker, as_of)
            if px is None:
                continue
            total += px * h.shares
            any_priced = True
        return total if any_priced else None
