"""MetricsService: builds compute_starter inputs from the DB and persists to computations."""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.db import Company, Repo, init_db
from finwatch.metrics.catalog import STARTER_METRICS
from finwatch.metrics.service import MetricsService

FX = Path(__file__).parent / "fixtures" / "companyfacts"


def _cf(tk):
    return json.loads((FX / f"{tk}.json").read_text())


def test_service_computes_and_persists_verbatim():
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="0000018230", ticker="CAT", sic_code="3531",
                                sector_class="general", is_financial=0, added_at="t"))
    svc = MetricsService(repo, lambda cik: _cf("CAT"), now_fn=lambda: "2025-05-01T00:00:00")

    bundle, n = svc.compute_and_store("0000018230", as_of="2025-05-01")

    assert set(bundle.results) == set(STARTER_METRICS)
    assert n == len(bundle.all_results()) == 6
    assert repo.count_computations("CAT") == 6

    comps = {c.tool: c for c in repo.list_computations("CAT")}
    revenue_row = comps["revenue_growth"]
    assert revenue_row.status == "unavailable"
    assert revenue_row.formula_version == "revenue_growth.v5"
    assert revenue_row.as_of == "2025-05-01"
    # result_json round-trips the MetricResult
    assert json.loads(revenue_row.result_json)["value"] == bundle.get("revenue_growth").value
    assert comps["share_count_change"].formula_version == "share_count_change.v4"


def test_service_marks_stale_msft_like_annual_sources_unavailable_with_provenance():
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="0000789019", ticker="MSFT", sic_code="7372",
                                sector_class="general", is_financial=0, added_at="t"))
    svc = MetricsService(repo, lambda _cik: _cf("MSFT"), now_fn=lambda: "t")

    result = svc.compute("0000789019", as_of="2025-05-01").get("revenue_growth")

    assert result.status.value == "unavailable"
    assert "current source is stale" in " ".join(result.unavailable_missing)
    assert len(result.inputs_used) == 2
    assert result.inputs_used[0].period_end == "2022-06-30"


def test_service_bank_persists_not_applicable_rows():
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="0000019617", ticker="JPM", sic_code="6021",
                                sector_class="financial", is_financial=1, added_at="t"))
    svc = MetricsService(repo, lambda cik: _cf("JPM"), now_fn=lambda: "t")
    _, n = svc.compute_and_store("0000019617", as_of="2025-05-01")
    comps = {c.tool: c for c in repo.list_computations("JPM")}
    assert comps["simple_leverage"].status == "not_applicable"
    assert set(comps) == set(STARTER_METRICS)
