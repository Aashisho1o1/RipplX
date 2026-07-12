"""The six-metric hand-verified starter suite.

Mix: two mega-cap non-financials (MSFT, GOOGL), a manufacturer (CAT), a bank (JPM →
financial not_applicable paths), and a messy small-cap (fallback tags + unavailable).
Expected values are hand-derived from the actual fixture XBRL; no metric ever raises —
only computed / unavailable / not_applicable. The launch product exposes exactly these
six metrics (compute_starter); the deferred valuation/scoring catalog is gone.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finwatch.core.types import MetricStatus, sector_from_sic
from finwatch.metrics.formulas import compute_starter
from finwatch.xbrl.normalize import FactStore

FX = Path(__file__).parent / "fixtures" / "companyfacts"
# After the newest synthetic fixture period: starter freshness gates must not
# mistake fixture look-ahead for a formula error in this hand-derived suite.
AS_OF = "2026-07-03"

# ticker -> SIC
COMPANIES = {
    "MSFT": "7372",   # software — non-financial
    "GOOGL": "7370",  # services — non-financial
    "CAT": "3531",    # machinery — manufacturer
    "JPM": "6021",    # bank — financial not_applicable paths
    "MESSY": "7389",  # small-cap, fallback tags, unavailable
}


def bundle_for(tk: str):
    store = FactStore.from_companyfacts(json.loads((FX / f"{tk}.json").read_text()))
    return compute_starter(store, sector_from_sic(COMPANIES[tk]), as_of=AS_OF)


# ---- universal invariant: never raise; exactly six valid results -----------
@pytest.mark.parametrize("tk", list(COMPANIES))
def test_no_metric_raises_and_every_status_is_valid(tk):
    b = bundle_for(tk)
    results = b.all_results()
    assert len(results) == 6
    valid = {MetricStatus.COMPUTED, MetricStatus.UNAVAILABLE, MetricStatus.NOT_APPLICABLE}
    for r in results:
        assert r.status in valid, f"{tk}/{r.metric}: {r.status}"
        assert r.formula_version and r.as_of == AS_OF
        if r.status == MetricStatus.NOT_APPLICABLE:
            assert r.not_applicable_reason
        if r.status == MetricStatus.UNAVAILABLE:
            assert r.unavailable_missing


# ---- mega-cap + manufacturer revenue growth (hand-derived) -----------------
def test_msft_revenue_growth_hand_derived():
    # FY25 revenue 281,724M vs FY24 245,122M -> (281724-245122)/245122 = 0.149322
    assert abs(bundle_for("MSFT").get("revenue_growth").value - 0.149322) < 1e-5


def test_cat_revenue_growth_hand_derived():
    # FY25 67,589M vs FY24 64,809M -> 0.042895
    assert abs(bundle_for("CAT").get("revenue_growth").value - 0.042895) < 1e-5


def test_cat_quarterly_net_income_gap_is_not_labeled_a_four_quarter_trend():
    # F11 regression: CAT tags recent QUARTERLY net income under the fallback tag
    # `ProfitLoss` while `NetIncomeLoss` (priority 1) carries the annual data. Per-accessor
    # tag resolution lets quarterly() fall through to ProfitLoss, but the fixture skips
    # Q4 between Q3 and the next Q1. A non-contiguous series must not be called a
    # four-quarter trend merely because four points exist.
    nit = bundle_for("CAT").get("net_income_trend")
    assert nit.status == MetricStatus.COMPUTED
    assert nit.components["yoy"] is not None
    assert nit.components["four_quarter_direction"] == "insufficient_points"


# ---- bank: financial-institution not_applicable paths ----------------------
def test_bank_simple_leverage_is_not_applicable():
    r = bundle_for("JPM").get("simple_leverage")
    assert r.status == MetricStatus.NOT_APPLICABLE
    assert r.not_applicable_reason == "financial_institution"


def test_bank_liquidity_not_applicable_on_balance_sheet():
    liq = bundle_for("JPM").get("liquidity_basics")
    assert liq.status == MetricStatus.NOT_APPLICABLE
    assert liq.not_applicable_reason == "financial_institution_balance_sheet"


# ---- messy small-cap: fallback tag resolution ------------------------------
def test_messy_revenue_resolves_via_fallback_tag():
    rg = bundle_for("MESSY").get("revenue_growth")
    assert rg.status == MetricStatus.COMPUTED
    assert abs(rg.value - 0.25) < 1e-9                # (50-40)/40
    assert rg.inputs_used[0].tag == "Revenues"        # 2nd-priority tag (primary absent)


def test_messy_shares_resolves_via_fallback_tag():
    sc = bundle_for("MESSY").get("share_count_change")
    assert sc.inputs_used[0].tag == "CommonStockSharesOutstanding"
