"""MetricsService: builds compute_all inputs from the DB and persists to computations."""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.db import Company, Holding, Repo, init_db
from finwatch.metrics.service import MetricsService

FX = Path(__file__).parent / "fixtures" / "companyfacts"


def _cf(tk):
    return json.loads((FX / f"{tk}.json").read_text())


class FakePrice:
    def __init__(self, prices):
        self.prices = prices

    def close_on_or_before(self, ticker, date_iso):
        return self.prices.get(ticker.upper())


def test_service_computes_and_persists_verbatim():
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="0000018230", ticker="CAT", sic_code="3531",
                                sector_class="general", is_financial=0, added_at="t"))
    svc = MetricsService(repo, FakePrice({"CAT": 350.0}), lambda cik: _cf("CAT"),
                         now_fn=lambda: "2025-05-01T00:00:00")

    bundle, n = svc.compute_and_store("0000018230", as_of="2025-05-01")

    # sector wired correctly -> manufacturer uses original Altman Z
    assert bundle.get("altman_z").components["variant"] == "Z"
    assert n == len(bundle.all_results()) == 16
    assert repo.count_computations("CAT") == 16

    comps = {c.tool: c for c in repo.list_computations("CAT")}
    az_row = comps["altman_z"]
    assert az_row.status == "computed"
    assert az_row.formula_version == "altman_z.v1"
    assert az_row.as_of == "2025-05-01"
    # result_json round-trips the MetricResult
    assert json.loads(az_row.result_json)["value"] == bundle.get("altman_z").value


def test_service_bank_persists_not_applicable_rows():
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="0000019617", ticker="JPM", sic_code="6021",
                                sector_class="financial", is_financial=1, added_at="t"))
    svc = MetricsService(repo, FakePrice({"JPM": 200.0}), lambda cik: _cf("JPM"),
                         now_fn=lambda: "t")
    _, n = svc.compute_and_store("0000019617", as_of="2025-05-01")
    comps = {c.tool: c for c in repo.list_computations("JPM")}
    assert comps["altman_z"].status == "not_applicable"
    assert comps["simple_leverage"].status == "not_applicable"


def test_service_owned_holding_yields_position_metrics():
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="0000789019", ticker="MSFT", sic_code="7372",
                                sector_class="general", is_financial=0, added_at="t"))
    repo.upsert_holding(Holding(cik="0000789019", ticker="MSFT", owned=1, shares=100,
                                cost_basis=300.0, target_weight_pct=10.0, added_at="t"))
    svc = MetricsService(repo, FakePrice({"MSFT": 450.0}), lambda cik: _cf("MSFT"),
                         now_fn=lambda: "t")
    bundle = svc.compute("0000789019", as_of="2025-05-01")

    pm = bundle.get("position_metrics")
    assert pm is not None and pm.status.value == "computed"
    # only MSFT is owned -> portfolio MV = 450*100 -> weight 100%; P/L = (450-300)/300
    assert abs(pm.components["weight_pct"] - 100.0) < 1e-6
    assert abs(pm.components["unrealized_pl_pct"] - 50.0) < 1e-6
    assert bundle.get("rebalance_check") is not None
