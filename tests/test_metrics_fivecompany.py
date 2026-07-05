"""Phase 3 DoD — the five-company hand-verified metric suite.

Mix: two mega-cap non-financials (MSFT, GOOGL), a classic manufacturer (CAT →
validates the ORIGINAL Altman Z), a large bank (JPM → every not_applicable path),
and a messy small-cap (fallback tags + unavailable/not_applicable). Expected values
are hand-derived from the actual fixture XBRL; no metric ever raises — only
computed / unavailable / not_applicable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finwatch.core.types import MetricStatus, sector_from_sic
from finwatch.metrics.formulas import compute_all
from finwatch.xbrl.normalize import FactStore

FX = Path(__file__).parent / "fixtures" / "companyfacts"
AS_OF = "2025-05-01"

# ticker -> (sic, fixed price or None)
COMPANIES = {
    "MSFT": ("7372", 450.0),    # software — non-manufacturer -> Z''
    "GOOGL": ("7370", 175.0),   # services — non-manufacturer -> Z''
    "CAT": ("3531", 350.0),     # machinery — manufacturer -> original Z
    "JPM": ("6021", 200.0),     # bank — financial not_applicable paths
    "MESSY": ("7389", None),    # small-cap, fallback tags, no price
}


class FakePrice:
    """Fixed close regardless of date (deterministic price-dependent metrics)."""

    def __init__(self, px: float | None) -> None:
        self.px = px

    def close_on_or_before(self, ticker: str, date_iso: str) -> float | None:
        return self.px


def bundle_for(tk: str):
    sic, px = COMPANIES[tk]
    store = FactStore.from_companyfacts(json.loads((FX / f"{tk}.json").read_text()))
    provider = FakePrice(px) if px is not None else None
    return compute_all(store, sector_from_sic(sic), ticker=tk,
                       price_provider=provider, as_of=AS_OF)


# ---- universal invariant: never raise; every status valid ------------------
@pytest.mark.parametrize("tk", list(COMPANIES))
def test_no_metric_raises_and_every_status_is_valid(tk):
    b = bundle_for(tk)
    results = b.all_results()
    assert len(results) == 16  # 13 named + 3 valuation percentiles (no holding)
    valid = {MetricStatus.COMPUTED, MetricStatus.UNAVAILABLE, MetricStatus.NOT_APPLICABLE}
    for r in results:
        assert r.status in valid, f"{tk}/{r.metric}: {r.status}"
        assert r.formula_version and r.as_of == AS_OF
        if r.status == MetricStatus.NOT_APPLICABLE:
            assert r.not_applicable_reason
        if r.status == MetricStatus.UNAVAILABLE:
            assert r.unavailable_missing


# ---- mega-cap non-financials -----------------------------------------------
def test_msft_revenue_growth_hand_derived():
    # FY25 revenue 281,724M vs FY24 245,122M -> (281724-245122)/245122 = 0.149322
    assert abs(bundle_for("MSFT").get("revenue_growth").value - 0.149322) < 1e-5


def test_msft_altman_double_prime_variant():
    az = bundle_for("MSFT").get("altman_z")
    assert az.status == MetricStatus.COMPUTED
    assert az.components["variant"] == "Z_double_prime"  # 7372 not a manufacturer SIC


def test_msft_piotroski_full_nine_components():
    pf = bundle_for("MSFT").get("piotroski_f")
    assert pf.components["components_evaluated"] == 9
    assert pf.components["score_scaled_9"] == 5


def test_googl_altman_double_prime_safe():
    az = bundle_for("GOOGL").get("altman_z")
    assert az.components["variant"] == "Z_double_prime"
    assert az.zone_or_flag == "safe"


# ---- manufacturer: ORIGINAL Altman Z ---------------------------------------
def test_cat_altman_original_z_with_price():
    # manufacturer SIC 3531 + price -> original Z. Components hand-verified:
    # 1.2*0.1326 + 1.4*0.7116 + 3.3*0.1167 + 0.6*(161,222,959,100/76,890,000,000)
    #   + 1.0*(67,589/95,550) = 3.5059
    az = bundle_for("CAT").get("altman_z")
    assert az.status == MetricStatus.COMPUTED
    assert az.components["variant"] == "Z"
    assert az.zone_or_flag == "safe"
    assert abs(az.value - 3.5059) < 1e-3


def test_cat_revenue_growth_hand_derived():
    # FY25 67,589M vs FY24 64,809M -> 0.042895
    assert abs(bundle_for("CAT").get("revenue_growth").value - 0.042895) < 1e-5


def test_cat_peg_not_applicable_on_negative_growth():
    peg = bundle_for("CAT").get("peg")
    assert peg.status == MetricStatus.NOT_APPLICABLE
    assert peg.not_applicable_reason == "non_positive_growth"


def test_cat_quarterly_net_income_falls_through_to_profitloss():
    # F11 regression: CAT tags recent QUARTERLY net income under the fallback tag
    # `ProfitLoss` while `NetIncomeLoss` (priority 1) carries the annual data. Per-accessor
    # tag resolution now lets quarterly() fall through to ProfitLoss, so the 4-quarter
    # direction is computed ('up') instead of being stranded as 'insufficient_points'.
    nit = bundle_for("CAT").get("net_income_trend")
    assert nit.status == MetricStatus.COMPUTED
    assert nit.components["yoy"] is not None  # headline YoY unchanged
    assert nit.components["four_quarter_direction"] == "up"


# ---- bank: every not_applicable path ---------------------------------------
def test_bank_financial_institution_not_applicable_paths():
    b = bundle_for("JPM")
    for metric in ("simple_leverage", "altman_z", "beneish_m", "fcf_yield"):
        r = b.get(metric)
        assert r.status == MetricStatus.NOT_APPLICABLE, metric
        assert r.not_applicable_reason == "financial_institution"
    vals = {v.metric: v for v in b.valuations}
    assert vals["valuation_pct_ev_ebitda"].status == MetricStatus.NOT_APPLICABLE
    assert vals["valuation_pct_p_fcf"].status == MetricStatus.NOT_APPLICABLE
    assert vals["valuation_pct_pe"].status == MetricStatus.COMPUTED  # P/E allowed


def test_bank_current_ratio_excluded_and_piotroski_reduced():
    b = bundle_for("JPM")
    liq = b.get("liquidity_basics")
    assert liq.status == MetricStatus.COMPUTED
    assert liq.components.get("current_ratio_note") == "not_applicable_financial_institution"
    pf = b.get("piotroski_f")
    assert pf.components["f6_current_ratio_improved"] == "skipped_financial"
    assert pf.components["f8_gross_margin_improved"] == "skipped_financial"
    assert pf.components["components_evaluated"] == 7  # 9 minus the 2 skipped


# ---- messy small-cap: fallback tags + unavailable/not_applicable ------------
def test_messy_revenue_resolves_via_fallback_tag():
    rg = bundle_for("MESSY").get("revenue_growth")
    assert rg.status == MetricStatus.COMPUTED
    assert abs(rg.value - 0.25) < 1e-9                # (50-40)/40
    assert rg.inputs_used[0].tag == "Revenues"        # 2nd-priority tag (primary absent)


def test_messy_shares_resolves_via_fallback_tag():
    sc = bundle_for("MESSY").get("share_count_change")
    assert sc.inputs_used[0].tag == "CommonStockSharesOutstanding"


def test_messy_negative_eps_graham_not_applicable():
    g = bundle_for("MESSY").get("graham_number")
    assert g.status == MetricStatus.NOT_APPLICABLE
    assert g.not_applicable_reason == "negative_eps_or_bvps"


def test_messy_altman_double_prime_distress_without_price():
    az = bundle_for("MESSY").get("altman_z")
    assert az.status == MetricStatus.COMPUTED
    assert az.components["variant"] == "Z_double_prime"
    assert az.zone_or_flag == "distress"
    assert abs(az.value - (-1.1263)) < 1e-3


def test_messy_beneish_unavailable_missing_tags():
    assert bundle_for("MESSY").get("beneish_m").status == MetricStatus.UNAVAILABLE
