"""Executable spec of the decision matrix. Trust-critical (test-guarded): edit with care, keep the spec tests green.
Self-contained: no network, no DB, no LLM."""
from __future__ import annotations

import pytest

from finwatch.core.types import CAUTION_ORDER, MetricStatus
from finwatch.metrics.envelope import MetricResult, MetricsBundle
from finwatch.signals.matrix import (Decision, ExtractionSummary, ImpactSummary,
                                     Record, apply_escalation, cap_toward_caution,
                                     evaluate)

AS_OF = "2026-07-03"


def mr(metric, status=MetricStatus.COMPUTED, value=None, zone=None,
       components=None, reason=None) -> MetricResult:
    return MetricResult(metric=metric, status=status, value=value,
                        zone_or_flag=zone, components=components or {},
                        not_applicable_reason=reason,
                        formula_version=f"{metric}.v1", as_of=AS_OF)


def bundle(*, f_scaled=None, z_zone=None, z_status=MetricStatus.COMPUTED,
           f_status=MetricStatus.COMPUTED, valuations=(), rebalance=None,
           f_components=None) -> MetricsBundle:
    b = MetricsBundle()
    if f_scaled is not None or f_status != MetricStatus.COMPUTED:
        comps = dict(f_components or {})
        if f_scaled is not None:
            comps["score_scaled_9"] = f_scaled
        b.results["piotroski_f"] = mr("piotroski_f", f_status,
                                      value=float(f_scaled or 0), components=comps)
    if z_zone is not None or z_status != MetricStatus.COMPUTED:
        b.results["altman_z"] = mr("altman_z", z_status, zone=z_zone,
                                   reason="financial_institution"
                                   if z_status == MetricStatus.NOT_APPLICABLE else None)
    for pct in valuations:
        b.valuations.append(mr("valuation_pct_pe", value=pct))
    if rebalance is not None:
        b.results["rebalance_check"] = mr("rebalance_check",
                                          zone="fires" if rebalance else "within_bands",
                                          value=1.0 if rebalance else 0.0)
    return b


def rec(**kw) -> Record:
    base = dict(ticker="TEST", owned=True, current_weight_pct=5.0,
                target_weight_pct=10.0, thesis="growth thesis")
    base.update(kw)
    return Record(**base)


CLEAN = ExtractionSummary()
NEUTRAL = ImpactSummary(thesis_verdict="intact", net_direction="neutral")


# ---- M0 -------------------------------------------------------------------
def test_watch_only_gate():
    d = evaluate(rec(owned=False), CLEAN, NEUTRAL, bundle())
    assert d.signal == "NOT_APPLICABLE_WATCHLIST" and d.posture is None


# ---- M1 fires with ZERO metrics (the bug the redesign fixed) ---------------
def test_critical_flag_fires_with_no_metrics_at_all():
    ext = ExtractionSummary(red_flag_codes=["going_concern"],
                            extraction_confidence="low", gaps=["mdna missing"])
    d = evaluate(rec(), ext, ImpactSummary(), MetricsBundle())
    assert d.signal == "STRONG_REVIEW_SELL"
    assert "M1" in d.rules_fired


def test_insufficient_data_only_when_unreadable_and_no_flags():
    ext = ExtractionSummary(extraction_confidence="low", gaps=["all sections"])
    d = evaluate(rec(), ext, ImpactSummary(), MetricsBundle())
    assert d.signal == "INSUFFICIENT_DATA"


def test_missing_metrics_alone_yield_hold_not_insufficient():
    d = evaluate(rec(), CLEAN, NEUTRAL, MetricsBundle())
    assert d.signal == "HOLD" and d.posture == "monitor"
    skipped = {s["rule"] for s in d.rules_skipped}
    assert {"M4", "M6", "M7"} <= skipped


# ---- M2 ---------------------------------------------------------------------
def test_thesis_broken_solvent_is_trim():
    d = evaluate(rec(), CLEAN, ImpactSummary(thesis_verdict="broken"),
                 bundle(f_scaled=8, z_zone="safe"))
    assert d.signal == "TRIM" and "M2" in d.rules_fired


def test_thesis_broken_with_bad_solvency_is_srs():
    d = evaluate(rec(), CLEAN, ImpactSummary(thesis_verdict="broken"),
                 bundle(f_scaled=2, z_zone="grey"))
    assert d.signal == "STRONG_REVIEW_SELL" and "M2a" in d.rules_fired


# ---- per-rule gates: a bank must not break the matrix ----------------------
def test_bank_not_applicable_altman_skips_m4_and_holds():
    b = bundle(f_scaled=5, z_status=MetricStatus.NOT_APPLICABLE)
    d = evaluate(rec(), CLEAN, NEUTRAL, b)
    assert d.signal == "HOLD"
    assert any(s["rule"] == "M4" and "not_applicable" in s["reason"]
               for s in d.rules_skipped)


# ---- M4 ----------------------------------------------------------------------
def test_solvency_deterioration_srs():
    b = bundle(f_scaled=2, z_zone="distress")
    d = evaluate(rec(), CLEAN,
                 ImpactSummary(thesis_verdict="intact", net_direction="negative"), b)
    assert d.signal == "STRONG_REVIEW_SELL" and "M4" in d.rules_fired


# ---- M6 ----------------------------------------------------------------------
def test_rich_and_deteriorating_trims():
    b = bundle(f_scaled=3, z_zone="safe", valuations=(95, 92, 50))
    d = evaluate(rec(), CLEAN, NEUTRAL, b)
    assert d.signal == "TRIM" and "M6" in d.rules_fired


def test_guidance_withdrawn_counts_as_deteriorating():
    b = bundle(f_scaled=8, z_zone="safe", valuations=(95, 92))
    d = evaluate(rec(), CLEAN,
                 ImpactSummary(thesis_verdict="intact", net_direction="neutral",
                               guidance_direction="withdrawn"), b)
    assert d.signal == "TRIM"


# ---- M7 ----------------------------------------------------------------------
def good_accumulate_bundle():
    return bundle(f_scaled=8, z_zone="safe", valuations=(20, 30, 35))


def test_accumulate_all_gates_pass():
    d = evaluate(rec(current_weight_pct=5.0, target_weight_pct=10.0),
                 CLEAN, NEUTRAL, good_accumulate_bundle())
    assert d.signal == "ACCUMULATE" and "M7" in d.rules_fired


def test_no_thesis_makes_m7_ineligible_but_never_blocks_hold():
    d = evaluate(rec(thesis=None), CLEAN,
                 ImpactSummary(thesis_verdict="not_assessable"),
                 good_accumulate_bundle())
    assert d.signal == "HOLD"
    assert any(s["rule"] == "M7" and s["reason"] == "no_thesis_provided"
               for s in d.rules_skipped)


def test_averaging_down_guard_blocks_accumulate():
    d = evaluate(rec(unrealized_pl_pct=-35.0), CLEAN, NEUTRAL,
                 good_accumulate_bundle())
    assert d.signal == "HOLD"
    assert any("averaging_down_guard" in s["reason"] for s in d.rules_skipped)


# ---- M5 cap: monotone toward caution only ------------------------------------
def test_concentration_caps_accumulate_to_trim():
    d = evaluate(rec(current_weight_pct=20.0, target_weight_pct=10.0),
                 CLEAN, NEUTRAL, good_accumulate_bundle())
    assert d.signal == "TRIM" and "M5" in d.caps_applied


def test_concentration_never_softens_srs():
    ext = ExtractionSummary(red_flag_codes=["item_4_02_non_reliance"])
    d = evaluate(rec(current_weight_pct=20.0), ext, NEUTRAL, bundle())
    assert d.signal == "STRONG_REVIEW_SELL"          # M1 short-circuits before caps


@pytest.mark.parametrize("base,floor,expect", [
    ("ACCUMULATE", "TRIM", "TRIM"), ("HOLD", "TRIM", "TRIM"),
    ("TRIM", "TRIM", "TRIM"), ("STRONG_REVIEW_SELL", "TRIM", "STRONG_REVIEW_SELL")])
def test_cap_monotone(base, floor, expect):
    assert cap_toward_caution(base, floor) == expect


# ---- escalation ----------------------------------------------------------------
def test_escalation_one_notch_toward_caution_only():
    d = evaluate(rec(), CLEAN, NEUTRAL, bundle(f_scaled=8, z_zone="safe"))
    assert d.signal == "HOLD"
    e = apply_escalation(d, "TRIM", "governance concerns")
    assert e.signal == "TRIM" and e.escalation["from"] == "HOLD"
    with pytest.raises(ValueError):
        apply_escalation(d, "ACCUMULATE", "nope")      # toward aggression
    with pytest.raises(ValueError):
        apply_escalation(d, "STRONG_REVIEW_SELL", "two notches")


# ---- property: caps never make the outcome less cautious ------------------------
def test_property_final_never_less_cautious_than_base():
    for w in (None, 5.0, 12.0, 20.0):
        for vals in ((), (95, 92), (20, 30, 35)):
            b = bundle(f_scaled=8, z_zone="safe", valuations=vals)
            d = evaluate(rec(current_weight_pct=w), CLEAN, NEUTRAL, b)
            if "M5" in d.caps_applied:
                assert CAUTION_ORDER.index(d.signal) <= CAUTION_ORDER.index("TRIM")
