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


def test_controlled_critical_flag_flows_through_dormant_adapter():
    from finwatch.llm.schemas import P1Output

    p1 = P1Output.model_validate({
        "accession_number": "a", "ticker": "T", "form_type": "8-K",
        "classification": {"overall_severity": "high"},
        "findings": [{
            "headline": "Going concern doubt", "severity": "high",
            "critical_flag": "going_concern",
            "evidence": [{"accession_number": "a", "form_type": "8-K",
                          "section_key": "item_8_01", "char_start": 0,
                          "char_end": 1, "snippet": "x"}],
        }],
        "extraction_confidence": "high", "gaps": []})
    ext = to_extraction_summary(p1)
    assert ext.has_red_flags and ext.red_flag_codes == ["going_concern"]
    d = evaluate(_under(), ext, _INTACT, _m7_passing_metrics())
    assert d.signal == "STRONG_REVIEW_SELL"


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


def test_v1_understands_unicode_minus_and_scientific_notation():
    unicode_minus = extract_number_tokens("Loss was −5%.")
    exponent = extract_number_tokens("Exposure was 1e9.")
    assert len(unicode_minus) == 1 and unicode_minus[0].value == -5.0
    assert len(exponent) == 1 and exponent[0].value == 1e9

    metrics = MetricsBundle(results={"x": _mr("x", value=1.0)})
    report = run_all(VerifyBundle(
        rendered_text="Exposure was 1e9.",
        metrics=metrics,
        disclaimer_text=DISCLAIMER,
    ))
    assert any(row.check_id == "V1" and row.verdict == "fail" for row in report.results)


def test_v1_exact_integer_does_not_use_blanket_relative_tolerance():
    metrics = MetricsBundle(results={"x": _mr("x", value=1_000_000_000.0)})
    report = run_all(VerifyBundle(
        rendered_text="Exposure was $1,000,400,000.",
        metrics=metrics,
        disclaimer_text=DISCLAIMER,
    ))
    assert any(row.check_id == "V1" and row.verdict == "fail" for row in report.results)


# ---- F9: V5 price-target regex covers the "$N target" form -------------------
def _v5(text):
    return check_v5_hygiene(VerifyBundle(rendered_text=text, metrics=MetricsBundle(),
                                         disclaimer_text=DISCLAIMER, trade_action=None))


def test_f9_dollar_target_form_is_caught_without_false_positive():
    assert any(c.verdict == "fail" for c in _v5("We assign a $50 target."))
    assert any(c.verdict == "fail" for c in _v5("price target of $50"))
    assert all(c.verdict == "pass" for c in _v5("drifting above its target weight"))


def test_v5_blocks_trade_recommendations_and_authored_valuation_but_not_exact_quotes():
    assert any(c.verdict == "fail" for c in _v5("You should sell the shares."))
    assert any(c.verdict == "fail" for c in _v5("We estimate a fair value of $50."))
    quoted = VerifyBundle(
        rendered_text='The filing says “shareholders should sell the shares.”',
        authored_text="Material tender-offer terms disclosed",
        metrics=MetricsBundle(),
        disclaimer_text=DISCLAIMER,
    )
    assert all(c.verdict == "pass" for c in check_v5_hygiene(quoted))


# ---- F4: point-in-time — historical analyses never use future-filed facts ---
def test_f4_as_of_excludes_facts_filed_after_the_cutoff():
    from finwatch.metrics.service import as_of_facts
    from finwatch.xbrl.normalize import FactStore

    def fy(year, val, filed):   # revenue is a FLOW -> needs a full-year duration
        return {"start": f"{year}-01-01", "end": f"{year}-12-31", "val": val, "filed": filed,
                "fy": year, "fp": "FY", "form": "10-K"}

    cf = {"cik": "1", "entityName": "X", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"start": "2021-01-01", "end": "2021-12-31", "val": 50,
         "fy": 2021, "fp": "FY", "form": "10-K"},  # unprovable: no filed date
        fy(2022, 100, "2023-02-01"),
        fy(2023, 200, "2024-02-01"),
        fy(2024, 300, "2025-02-01"),   # filed AFTER the as_of below
    ]}}}}}
    filtered = as_of_facts(cf, "2024-06-01")
    kept = {e["end"] for e in filtered["facts"]["us-gaap"]["Revenues"]["units"]["USD"]}
    assert kept == {"2022-12-31", "2023-12-31"}  # future-filed and undated facts excluded
    store = FactStore.from_companyfacts(filtered)
    assert store.latest_annual("revenue").fact.value == 200.0   # not 300 (a future filing)


def test_companyfacts_unit_selection_is_deterministic_and_ambiguous_units_fail_closed():
    from finwatch.xbrl.normalize import FactStore

    def entry(end, value):
        year = int(end[:4])
        return {
            "start": f"{year}-01-01",
            "end": end,
            "val": value,
            "filed": f"{year + 1}-02-01",
            "accn": f"{year}",
            "form": "10-K",
            "fy": year,
            "fp": "FY",
        }

    units = {
        "EUR": [entry("2023-12-31", 900), entry("2022-12-31", 800)],
        "USD": [entry("2023-12-31", 200), entry("2022-12-31", 100)],
    }
    payload = {"facts": {"us-gaap": {"Revenues": {"units": units}}}}
    pair = FactStore.from_companyfacts(payload).yoy_pair("revenue")
    assert pair is not None
    assert [row.fact.unit for row in pair] == ["USD", "USD"]
    assert [row.fact.value for row in pair] == [200.0, 100.0]

    ambiguous = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "EUR": units["EUR"],
                        "GBP": [entry("2023-12-31", 700), entry("2022-12-31", 600)],
                    }
                }
            }
        }
    }
    assert FactStore.from_companyfacts(ambiguous).latest_annual("revenue") is None


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

    # A real, aligned imbalance is surfaced as a non-blocking WARNING, not a blocking fail:
    # V2 validates DATA and the raw identities false-fail on legitimate structures (NCI in
    # equity, restricted cash) too often to quarantine a whole filing on them.
    imbalanced = audit(_bs("2023-12-31", "2023-12-31", "2023-12-31", 100, 60, 50), "10-K")
    assert imbalanced["V2a"].verdict == "warn" and imbalanced["V2a"].severity == "warning"


def test_review_v2_nci_imbalance_does_not_block_the_filing():
    # Remediation-review regression: a healthy consolidated issuer with noncontrolling
    # interest (A = L + parent-equity + NCI) breaks the parent-only A=L+E identity, but must
    # NOT escalate to manual review — it is a data-quality warning only.
    from finwatch.core.types import sector_from_sic
    from finwatch.verify.checks import run_all
    from finwatch.verify.orchestrator import data_quality_report
    from finwatch.xbrl.normalize import FactStore

    store = FactStore.from_companyfacts(
        _bs("2024-12-31", "2024-12-31", "2024-12-31", 10000, 6000, 3600))  # 400 of NCI
    v2 = data_quality_report(store, sector_from_sic("7372"), form_type="10-K")
    assert {r.check_id for r in v2} >= {"V2a"}
    assert all(not (r.verdict == "fail" and r.severity == "blocking") for r in v2)
    # combined with a clean LLM-gate report, the verdict is not FAIL (no manual review)
    from finwatch.metrics.envelope import MetricsBundle
    from finwatch.verify.checks import VerifyBundle
    llm_gate = run_all(VerifyBundle(rendered_text="", metrics=MetricsBundle(),
                                    disclaimer_text=DISCLAIMER, trade_action=None))
    combined = list(llm_gate.results) + list(v2)
    assert not any(c.verdict == "fail" and c.severity == "blocking" for c in combined)


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


def test_revenue_resolves_to_freshest_tag_across_a_tag_migration():
    # An issuer that migrated RevenueFromContract... → Revenues: the abandoned tag's
    # stale value must NOT be presented as current. Resolve to the freshest tag.
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": end, "form": "10-K"}

    cf = {"cik": "1", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            dur("2021-01-01", "2021-12-31", 300)]}},         # abandoned, stale
        "Revenues": {"units": {"USD": [
            dur("2023-01-01", "2023-12-31", 500),            # current
            dur("2022-01-01", "2022-12-31", 400)]}}}}}
    store = FactStore.from_companyfacts(cf)
    latest = store.latest_annual("revenue")
    assert latest.fact.tag == "Revenues" and latest.fact.value == 500.0
    cur, prior = store.yoy_pair("revenue")
    assert (cur.fact.value, prior.fact.value) == (500.0, 400.0)  # same tag, no splicing


def test_revenue_conflict_at_newest_period_fails_closed():
    # Two revenue tags claim the SAME newest period-end with DIFFERENT values (total vs a
    # contract-revenue subset). We cannot know which the user means → unavailable.
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": end, "form": "10-K"}

    cf = {"cik": "1", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            dur("2024-01-01", "2024-12-31", 300)]}},
        "Revenues": {"units": {"USD": [
            dur("2024-01-01", "2024-12-31", 999)]}}}}}
    assert FactStore.from_companyfacts(cf).latest_annual("revenue") is None


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


# ---- YoY annual pairing requires ~1yr spacing (no mislabeled multi-year delta) ----
def test_yoy_annual_pair_requires_one_year_spacing():
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": end, "form": "10-K"}

    # Contiguous fiscal years -> the two newest annuals ARE the YoY pair.
    contiguous = {"cik": "1", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        dur("2022-01-01", "2022-12-31", 180),
        dur("2023-01-01", "2023-12-31", 200)]}}}}}
    pair = FactStore.from_companyfacts(contiguous).yoy_pair("revenue", "annual")
    assert pair is not None
    assert (pair[0].fact.value, pair[1].fact.value) == (200.0, 180.0)

    # Fiscal-year change: the newest full FY ends 2025-06-30, the prior full FY ends
    # 2023-12-31 (the ~6-month transition stub is sub-annual and excluded from annual()).
    # Those two annuals are ~18 months apart; pairing them blindly would compute an
    # 18-month change labelled "YoY". The spacing guard must return None (unavailable).
    gapped = {"cik": "1", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        dur("2023-01-01", "2023-12-31", 180),
        dur("2024-07-01", "2025-06-30", 240)]}}}}}
    assert FactStore.from_companyfacts(gapped).yoy_pair("revenue", "annual") is None


# ---- V1 scale-branch gating: a coarse suffixed number cannot cross-scale-match ----
def _v1(text, m):
    bundle = VerifyBundle(rendered_text=text, metrics=m, disclaimer_text=DISCLAIMER,
                          trade_action=None)
    return next(c for c in run_all(bundle).results if c.check_id == "V1").verdict


def test_v1_coarse_billion_does_not_match_unrelated_fact_via_x100():
    m = MetricsBundle()
    m.results["exposure"] = _mr("exposure", value=30_000_000.0)     # unrelated $30M fact
    # "$3 billion" (3e9) must NOT verify against a $30M fact via the ×100 branch.
    assert _v1("Exposure of roughly $3 billion.", m) == "fail"
    # ...but a genuine ~$3B fact still verifies it (coarse rounding is real provenance).
    m2 = MetricsBundle()
    m2.results["exposure"] = _mr("exposure", value=2_700_000_000.0)  # $2.7B rounds to $3B
    assert _v1("Exposure of roughly $3 billion.", m2) == "pass"


def test_v1_percent_token_still_matches_ratio_fact_via_x100():
    m = MetricsBundle()
    m.results["rev_growth"] = _mr("rev_growth", value=0.081)         # ratio leaf
    assert _v1("Revenue grew 8.1%.", m) == "pass"


def test_v1_comma_grouped_count_in_year_range_is_not_whitelisted_as_year():
    # "2,000" (comma-grouped) is a count, not a year: it is provenance-checked.
    toks = extract_number_tokens("The restructuring affects roughly 2,000 employees.")
    assert any(abs(t.value - 2000.0) < 1e-9 for t in toks)
    # A genuine bare fiscal year is still whitelisted (year context before it).
    assert all(t.value != 2024.0 for t in extract_number_tokens("In fiscal 2024 revenue rose."))


def test_v1_reference_whitelist_is_anchored_not_substring():
    # A real count that merely appears near an 'Item N' reference (not immediately
    # preceded by the keyword) must still be provenance-checked.
    assert any(abs(t.value - 5.0) < 1e-9
               for t in extract_number_tokens("Item 2, and 5 sites were affected"))
    # An actual reference code (keyword immediately before it) is still whitelisted.
    assert all(t.value != 2.0 for t in extract_number_tokens("see Item 2 of the report"))


# ---- Piotroski f5: unpaired-but-present debt must not be credited as deleveraging ----
def _piotroski_cf(lt_debt_instants):
    def dur(y, val):
        return {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": val,
                "filed": f"{y + 1}-02-01", "form": "10-K"}

    def inst(end, val):
        return {"end": end, "val": val, "filed": end, "form": "10-K"}

    facts = {
        "NetIncomeLoss": {"units": {"USD": [dur(2023, 100), dur(2024, 120)]}},
        "Assets": {"units": {"USD": [inst("2023-12-31", 1000), inst("2024-12-31", 1100)]}},
        "NetCashProvidedByUsedInOperatingActivities":
            {"units": {"USD": [dur(2023, 150), dur(2024, 170)]}},
        "Revenues": {"units": {"USD": [dur(2023, 900), dur(2024, 1000)]}},
    }
    if lt_debt_instants:
        facts["LongTermDebtNoncurrent"] = {
            "units": {"USD": [inst(e, v) for e, v in lt_debt_instants]}}
    return {"cik": "1", "facts": {"us-gaap": facts}}


def test_f5_present_but_unpaired_debt_is_skipped_not_awarded():
    from finwatch.core.types import sector_from_sic
    from finwatch.metrics.formulas import piotroski_f
    from finwatch.xbrl.normalize import FactStore

    # LT debt exists (one instant) but has no year-prior pair -> f5 must be skipped,
    # NOT awarded (the old code credited a deleveraging point on missing data).
    store = FactStore.from_companyfacts(_piotroski_cf([("2024-12-31", 400)]))
    r = piotroski_f(store, sector_from_sic("3711"), "2025-06-01")
    assert r.status.value == "computed"
    assert r.components["f5_leverage_decreased"] == "skipped"
    assert r.components.get("f5_note") == "lt_debt_present_but_unpaired_skipped"


def test_f5_truly_absent_debt_still_counts_as_pass():
    from finwatch.core.types import sector_from_sic
    from finwatch.metrics.formulas import piotroski_f
    from finwatch.xbrl.normalize import FactStore

    store = FactStore.from_companyfacts(_piotroski_cf([]))
    r = piotroski_f(store, sector_from_sic("3711"), "2025-06-01")
    assert r.status.value == "computed"
    assert r.components["f5_leverage_decreased"] is True
    assert r.components.get("f5_note") == "no_lt_debt_reported_treated_as_pass"
