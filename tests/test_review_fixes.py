"""Regression tests for the external-review fixes that touch the Tier 1 core (G2).

These sit outside the frozen executable-spec batteries (test_signals_matrix.py /
test_verifier_mutations.py stay pristine); each test is named for the finding it guards.
"""
from __future__ import annotations

from finwatch.core.types import DISCLAIMER
from finwatch.metrics.envelope import MetricResult, MetricsBundle, MetricStatus
from finwatch.pipeline.adapters import to_extraction_summary
from finwatch.signals.matrix import (
    ExtractionSummary,
    ImpactSummary,
    Record,
    evaluate,
)
from finwatch.verify.checks import (
    VerifyBundle,
    check_v5_hygiene,
    extract_number_tokens,
    run_all,
)


def _mr(metric, **kw):
    return MetricResult(metric=metric, status=MetricStatus.COMPUTED,
                        formula_version=f"{metric}.v1", as_of="t", **kw)


def _m7_passing_metrics(valuations=(20.0, 20.0), rebalance=None):
    b = MetricsBundle()
    b.results["piotroski_f"] = _mr("piotroski_f", components={
        "score_scaled_9": 8, "f3_delta_roa_positive": True, "f8_gross_margin_improved": True})
    b.results["altman_z"] = _mr("altman_z", zone_or_flag="safe")
    b.valuations = [_mr("valuation_percentile", value=v) for v in valuations]
    if rebalance is not None:
        b.results["rebalance_check"] = _mr("rebalance_check", zone_or_flag=rebalance)
    return b


def _under() -> Record:               # underweight, thesis intact, M7-eligible
    return Record(ticker="T", owned=True, current_weight_pct=5.0, target_weight_pct=10.0,
                  thesis="growth compounds")


_INTACT = ImpactSummary(thesis_verdict="intact", net_direction="neutral")


# ---- F16: 0th percentile is a valid (cheapest) percentile, not "missing" ----
def test_f16_zeroth_percentile_counts_toward_accumulate_gate():
    d = evaluate(_under(), ExtractionSummary(), _INTACT,
                 _m7_passing_metrics(valuations=(0.0, 20.0)))
    assert d.signal == "ACCUMULATE" and "M7" in d.rules_fired


# ---- F5: M5 concentration cap fires only on OVER-weight drift ----------------
def test_f5_underweight_drift_does_not_trip_concentration_cap():
    d = evaluate(_under(), ExtractionSummary(), _INTACT,
                 _m7_passing_metrics(valuations=(20.0, 20.0), rebalance="fires"))
    assert d.signal == "ACCUMULATE" and "M5" not in d.rules_fired   # not capped to TRIM


def test_f5_overweight_drift_still_caps_toward_caution():
    over = Record(ticker="T", owned=True, current_weight_pct=13.0, target_weight_pct=10.0,
                  thesis="t")   # over target but < 15% and < 1.5×target
    d = evaluate(over, ExtractionSummary(), _INTACT,
                 _m7_passing_metrics(rebalance="fires"))
    assert d.signal == "TRIM" and "M5" in d.rules_fired


# ---- F6: any red flag (even non-critical) blocks ACCUMULATE ------------------
def test_f6_any_red_flag_blocks_m7_accumulate():
    d = evaluate(_under(), ExtractionSummary(has_red_flags=True), _INTACT,
                 _m7_passing_metrics())
    assert d.signal != "ACCUMULATE"
    assert any(s["rule"] == "M7" and "red_flags" in s["reason"] for s in d.rules_skipped)


def test_f6_high_covenant_flag_flows_through_adapter_and_blocks_m7():
    from finwatch.llm.schemas import P1Output

    p1 = P1Output.model_validate({
        "accession_number": "a", "ticker": "T", "form_type": "8-K",
        "classification": {"items_8k": [], "overall_severity": "high"},
        "claims": [], "material_items": [],
        "guidance_direction": {"value": "none_stated"},
        "red_flags": [{"flag": "covenant breach and waiver", "severity": "high"}],
        "extraction_confidence": "high", "gaps": []})
    ext = to_extraction_summary(p1)
    assert ext.has_red_flags and ext.red_flag_codes == []   # non-critical -> not in codes
    d = evaluate(_under(), ext, _INTACT, _m7_passing_metrics())
    assert d.signal != "ACCUMULATE"


# ---- F7: V3 re-derives the FULL decision (posture, rules_skipped, caps) ------
def _v3(decision, record, extraction, impact, metrics):
    bundle = VerifyBundle(rendered_text="", metrics=metrics, decision=decision, record=record,
                          extraction=extraction, impact=impact, disclaimer_text=DISCLAIMER,
                          trade_action=None)
    return next(c for c in run_all(bundle).results if c.check_id == "V3")


def test_f7_v3_catches_tampered_posture_skipped_and_caps():
    rec, ext, imp, m = _under(), ExtractionSummary(), ImpactSummary(), MetricsBundle()
    good = evaluate(rec, ext, imp, m)
    assert _v3(good, rec, ext, imp, m).verdict == "pass"        # honest decision passes

    tampered_posture = good.model_copy(deep=True)
    tampered_posture.posture = "positive_support"
    assert _v3(tampered_posture, rec, ext, imp, m).verdict == "fail"

    tampered_skipped = good.model_copy(deep=True)
    tampered_skipped.rules_skipped = [{"rule": "FAKE", "reason": "x"}]
    assert _v3(tampered_skipped, rec, ext, imp, m).verdict == "fail"

    tampered_caps = good.model_copy(deep=True)
    tampered_caps.caps_applied = ["FAKE_CAP"]
    assert _v3(tampered_caps, rec, ext, imp, m).verdict == "fail"


# ---- F8: V1 tokenizer is sign-aware (leading minus) --------------------------
def test_f8_leading_minus_is_negative_but_ranges_are_not():
    assert any(abs(t.value + 5.0) < 1e-9 for t in extract_number_tokens("Loss was -5%."))
    assert sorted(t.value for t in extract_number_tokens("a 5-10 range")) == [5.0, 10.0]


def test_f8_v1_flags_a_sign_reversed_number():
    m = MetricsBundle()
    m.results["x"] = _mr("x", value=5.0)                        # candidate +5
    bundle = VerifyBundle(rendered_text="Loss was -5%.", metrics=m, disclaimer_text=DISCLAIMER,
                          trade_action=None)
    v1 = next(c for c in run_all(bundle).results if c.check_id == "V1")
    assert v1.verdict == "fail"                                # -5 does not match +5


# ---- F9: V5 price-target regex covers the "$N target" form -------------------
def _v5(text):
    return check_v5_hygiene(VerifyBundle(rendered_text=text, metrics=MetricsBundle(),
                                         disclaimer_text=DISCLAIMER, trade_action=None))


def test_f9_dollar_target_form_is_caught_without_false_positive():
    assert any(c.verdict == "fail" for c in _v5("We assign a $50 target."))
    assert any(c.verdict == "fail" for c in _v5("price target of $50"))
    assert all(c.verdict == "pass" for c in _v5("drifting above its target weight"))


# ---- F4: point-in-time — historical analyses never use future-filed facts ---
def test_f4_as_of_excludes_facts_filed_after_the_cutoff():
    from finwatch.metrics.service import as_of_facts
    from finwatch.xbrl.normalize import FactStore

    def fy(year, val, filed):   # revenue is a FLOW -> needs a full-year duration
        return {"start": f"{year}-01-01", "end": f"{year}-12-31", "val": val, "filed": filed,
                "fy": year, "fp": "FY", "form": "10-K"}

    cf = {"cik": "1", "entityName": "X", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        fy(2022, 100, "2023-02-01"),
        fy(2023, 200, "2024-02-01"),
        fy(2024, 300, "2025-02-01"),   # filed AFTER the as_of below
    ]}}}}}
    filtered = as_of_facts(cf, "2024-06-01")
    kept = {e["end"] for e in filtered["facts"]["us-gaap"]["Revenues"]["units"]["USD"]}
    assert kept == {"2022-12-31", "2023-12-31"}         # the 2025-filed fact is excluded
    store = FactStore.from_companyfacts(filtered)
    assert store.latest_annual("revenue").fact.value == 200.0   # not 300 (a future filing)


# ---- F10: V2 accounting identities run (V2b annual-gated, V2a alignment-gated) --
def _bs(assets_end, liab_end, equity_end, assets, liab, equity):
    def inst(end, val):
        return {"end": end, "val": val, "filed": "2024-02-01", "fy": 2023, "fp": "FY",
                "form": "10-K"}
    return {"cik": "1", "entityName": "X", "facts": {"us-gaap": {
        "Assets": {"units": {"USD": [inst(assets_end, assets)]}},
        "Liabilities": {"units": {"USD": [inst(liab_end, liab)]}},
        "StockholdersEquity": {"units": {"USD": [inst(equity_end, equity)]}}}}}


def test_f10_v2_runs_with_annual_and_alignment_gates():
    from finwatch.core.types import sector_from_sic
    from finwatch.verify.orchestrator import data_quality_report
    from finwatch.xbrl.normalize import FactStore

    def audit(cf, form):
        store = FactStore.from_companyfacts(cf)
        return {r.check_id: r for r in data_quality_report(store, sector_from_sic("7372"),
                                                           form_type=form)}

    aligned = audit(_bs("2023-12-31", "2023-12-31", "2023-12-31", 100, 60, 40), "10-Q")
    assert aligned["V2a"].verdict == "pass"                          # A = L + E, same period
    assert aligned["V2b"].verdict == "skipped_not_applicable"        # 10-Q -> V2b n/a

    misaligned = audit(_bs("2023-12-31", "2023-12-31", "2022-12-31", 100, 60, 40), "10-Q")
    assert misaligned["V2a"].verdict == "skipped_not_applicable"     # different period-ends

    imbalanced = audit(_bs("2023-12-31", "2023-12-31", "2023-12-31", 100, 60, 50), "10-K")
    assert imbalanced["V2a"].verdict == "fail"                       # real A != L + E, aligned


# ---- F13: incomplete price coverage -> weights unavailable ------------------
def test_f13_unpriced_holding_makes_portfolio_weights_unavailable():
    from finwatch.db import Company, Holding, Price, Repo, init_db
    from finwatch.metrics.service import MetricsService

    repo = Repo(init_db(":memory:"))
    for cik, tkr in (("1", "AAA"), ("2", "BBB")):
        repo.upsert_company(Company(cik=cik, ticker=tkr, sic_code="7372", is_financial=0,
                                    added_at="t"))
        repo.upsert_holding(Holding(cik=cik, ticker=tkr, owned=1, shares=10, cost_basis=1.0,
                                    added_at="t"))
    repo.upsert_prices([Price(ticker="AAA", date="2024-01-01", close=100.0)])   # BBB unpriced
    svc = MetricsService(repo, repo, lambda _c: {"facts": {}}, now_fn=lambda: "t")
    assert svc._portfolio_market_value("2024-06-01") is None        # incomplete coverage
    repo.upsert_prices([Price(ticker="BBB", date="2024-01-01", close=100.0)])
    assert svc._portfolio_market_value("2024-06-01") == 2000.0      # both priced


# ---- F15: golden gate scores recall through the real severity-gated adapter --
def test_f15_recall_uses_critical_code_severity_gate():
    from finwatch.pipeline.adapters import critical_code

    # the harness now computes found = {critical_code(flag, severity) ...}
    assert {c for (f, s) in [("going_concern", "low")] if (c := critical_code(f, s))} == set()
    assert {c for (f, s) in [("going_concern", "critical")]
            if (c := critical_code(f, s))} == {"going_concern"}
    # a natural-language phrasing at critical severity still resolves (no false negative)
    assert critical_code("substantial doubt about going concern", "critical") == "going_concern"


# ---- F11: concept->tag resolves PER ACCESSOR (falls through to a usable tag) --
def test_f11_annual_falls_through_to_usable_fallback_tag():
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": "2024-02-01", "form": "10-Q"}

    cf = {"cik": "1", "facts": {"us-gaap": {
        # priority-1 revenue tag carries ONLY a quarter; the fallback carries the annuals
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            dur("2023-10-01", "2023-12-31", 50)]}},
        "Revenues": {"units": {"USD": [
            dur("2022-01-01", "2022-12-31", 180), dur("2023-01-01", "2023-12-31", 200)]}}}}}
    store = FactStore.from_companyfacts(cf)
    assert [r.fact.value for r in store.annual("revenue")] == [200.0, 180.0]   # fell through
    assert store.quarterly("revenue")[0].fact.value == 50.0                    # primary tag


# ---- F12: valuation history uses period-matched shares/net debt -------------
class _FlatPrice:
    def close_on_or_before(self, ticker, date_iso):
        return 10.0


def _pe_facts(share_series):
    def dur(y, v):
        return {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "filed": f"{y + 1}-02-01",
                "form": "10-K"}

    def inst(end, v):
        return {"end": end, "val": v, "filed": end, "form": "10-K"}

    return {"cik": "1", "facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [dur(y, 100) for y in (2020, 2021, 2022, 2023)]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            inst(end, v) for end, v in share_series]}}}}}


def test_f12_history_uses_period_matched_shares():
    from finwatch.core.types import sector_from_sic
    from finwatch.metrics.formulas import valuation_percentile
    from finwatch.xbrl.normalize import FactStore

    # shares doubled this year -> today's P/E is RICH vs the (period-matched) history.
    store = FactStore.from_companyfacts(_pe_facts([
        ("2020-12-31", 100), ("2021-12-31", 100), ("2022-12-31", 100), ("2023-12-31", 200)]))
    r = valuation_percentile(store, sector_from_sic("7372"), "2024-01-01", ticker="T",
                             price_provider=_FlatPrice(), multiple="pe")
    assert r.status.value == "computed"
    assert r.components["history_capital_structure"] == "period_matched"
    assert r.confidence == "medium"
    assert r.value == 100.0        # reusing today's 200 shares would have (wrongly) given 0.0


def test_f12_falls_back_to_current_shares_with_low_confidence():
    from finwatch.core.types import sector_from_sic
    from finwatch.metrics.formulas import valuation_percentile
    from finwatch.xbrl.normalize import FactStore

    # only the latest share count exists -> history approximated with it, confidence drops.
    store = FactStore.from_companyfacts(_pe_facts([("2023-12-31", 200)]))
    r = valuation_percentile(store, sector_from_sic("7372"), "2024-01-01", ticker="T",
                             price_provider=_FlatPrice(), multiple="pe")
    assert r.status.value == "computed"
    assert r.components["history_capital_structure"] == "current_fallback"
    assert r.confidence == "low"
