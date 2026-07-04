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
