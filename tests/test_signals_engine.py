"""Phase 6 signal engine: adapters, decide→rationale→shadow, V3 re-derivation, escalation.

(The matrix itself + the caps-toward-caution property are covered by the Tier 1
test_signals_matrix.py; here we test the WIRING around it.)
"""
from __future__ import annotations

import json

from finwatch.core.types import CRITICAL_DOC_FLAGS, DISCLAIMER
from finwatch.db import Holding, Repo, SignalShadowLog, init_db
from finwatch.llm.router import FakeLLMClient
from finwatch.llm.schemas import P1Output, P2Output
from finwatch.metrics.envelope import MetricResult, MetricsBundle, MetricStatus
from finwatch.pipeline.adapters import (
    critical_code,
    to_extraction_summary,
    to_impact_summary,
    to_record,
)
from finwatch.signals.engine import SignalEngine, render_shadow_report
from finwatch.signals.matrix import ExtractionSummary, ImpactSummary, Record, evaluate
from finwatch.verify.checks import VerifyBundle, run_all


def _p1(sev, flags, conf="high", gaps=None):
    return P1Output.model_validate({
        "accession_number": "a", "ticker": "T", "form_type": "10-K",
        "classification": {"overall_severity": sev},
        "findings": [{
            "headline": "Critical filing concern",
            "severity": "critical",
            "critical_flag": f,
            "evidence": [{"accession_number": "a", "form_type": "10-K",
                          "section_key": "controls", "char_start": index,
                          "char_end": index + 1, "snippet": "x"}],
        } for index, f in enumerate(flags)],
        "extraction_confidence": conf,
        "gaps": gaps or []})


def _p3_json(**over):
    d = {"ticker": "T", "accession_number": "a", "review_posture": "monitor",
         "trade_action": None, "hypothetical_signal": "HOLD", "rules_fired": [],
         "rules_skipped": [], "computed_inputs": [], "rationale": "r",
         "counter_evidence": "c", "what_would_change_this": ["x"], "confidence": "high",
         "disclaimer": DISCLAIMER}
    d.update(over)
    return json.dumps(d)


class _FakePrice:
    def close_on_or_before(self, ticker, date_iso):
        return 42.0


def _engine(repo, p3_json=None):
    llm = FakeLLMClient(responder=lambda _s, _u: p3_json or _p3_json())
    return SignalEngine(repo, llm, price_provider=_FakePrice(), model_label="fake",
                        now_fn=lambda: "t")


# ---- adapters: red-flag → critical-code mapping ----------------------------
def test_critical_code_resolves_canonical_codes_at_their_severity():
    # a canonical code at its genuine (≥HIGH / cyber-critical) severity resolves to itself;
    # an unrecognized flag is never critical.
    assert critical_code("item_1_03_bankruptcy", "critical") == "item_1_03_bankruptcy"
    assert critical_code("material_weakness_with_restatement_risk", "high") == \
        "material_weakness_with_restatement_risk"
    assert critical_code("cyber_1_05_critical_tier", "critical") == "cyber_1_05_critical_tier"
    assert critical_code("some_other_flag", "critical") is None


def test_critical_code_matches_descriptive_phrasings_at_hard_floor_severity():
    # gate #2 (100% critical recall): the verbose phrasings a live model actually emits — down
    # to named debt instruments and exchange-specific listing rules — must resolve at ≥ HIGH.
    cases = {
        "Substantial doubt about the Company's ability to continue as a going concern":
            "going_concern",
        "Material weaknesses in internal control over financial reporting":
            "material_weakness_with_restatement_risk",
        "Previously issued financial statements should no longer be relied upon":
            "item_4_02_non_reliance",
        "Restatement of previously issued financial statements": "item_4_02_non_reliance",
        "Resignation of the independent registered public accounting firm": "auditor_resignation",
        "Chapter 11 bankruptcy filing": "item_1_03_bankruptcy",
        "Nasdaq delisting notice": "item_3_01_delisting",
        "Notice of noncompliance with Nasdaq Listing Rule 5550 (minimum bid price)":
            "item_3_01_delisting",
        "Acceleration of indebtedness": "item_2_04_acceleration",
        "Acceleration of the 5.5% Senior Notes due 2029": "item_2_04_acceleration",
    }
    for phrase, code in cases.items():
        assert critical_code(phrase, "high") == code, phrase
        assert critical_code(phrase, "critical") == code, phrase


def test_critical_code_cyber_tier_requires_critical_severity():
    # Item 1.05 base is HIGH; only a MATERIAL incident is the critical tier.
    assert critical_code("Ransomware attack", "critical") == "cyber_1_05_critical_tier"
    assert critical_code("cybersecurity incident", "critical") == "cyber_1_05_critical_tier"
    assert critical_code("cybersecurity incident", "high") is None   # non-material → not critical


def test_critical_code_severity_gate_silences_boilerplate():
    # gate #4 (boring-filing silence): the SAME lexicon words appear as routine risk-factor
    # boilerplate rated below the bar — including collisions that equal a canonical code verbatim
    # ("going concern"/"auditor resignation" negative-assurance language) — must NOT fire M1.
    for phrase in ("unauthorized access", "ransomware", "delisting", "restatement",
                   "material weakness", "going concern", "auditor resignation"):
        assert critical_code(phrase, "low") is None
        assert critical_code(phrase, "medium") is None


def test_critical_code_excludes_lookalikes_even_at_critical_severity():
    # AND-guards: benign look-alikes never map, even if P1 mis-rates them critical.
    for phrase in ("revenue acceleration", "earnings acceleration", "cfo resigned",
                   "officer resigned", "impairment", "covenant waiver"):
        assert critical_code(phrase, "critical") is None


def test_going_concern_controlled_flag_fires_m1_end_to_end():
    ext = to_extraction_summary(_p1("critical", ["going_concern"]))
    assert set(ext.red_flag_codes) & CRITICAL_DOC_FLAGS   # non-empty intersection
    d = evaluate(Record(ticker="T", owned=True, thesis="th"), ext, ImpactSummary(),
                 MetricsBundle())
    assert d.signal == "STRONG_REVIEW_SELL" and "M1" in d.rules_fired


def test_to_extraction_summary_normalizes_flags():
    ext = to_extraction_summary(_p1("critical", ["item_4_02_non_reliance"], gaps=["g"]))
    assert ext.red_flag_codes == ["item_4_02_non_reliance"]
    assert ext.extraction_confidence == "high" and ext.gaps == ["g"]


def test_to_impact_summary_from_p2_and_none():
    p2 = P2Output.model_validate({"accession_number": "a", "records_affected": [{
        "ticker": "T", "owned": True, "impact_class": "direct", "channels": {},
        "guidance_direction": "lowered", "liquidity_read": "deteriorating",
        "net_direction": "negative", "thesis_check": {"verdict": "broken"},
        "net_read": {"text": "x"}, "confidence": "high"}]})
    imp = to_impact_summary(p2, "T")
    assert (imp.thesis_verdict, imp.net_direction, imp.guidance_direction) == \
        ("broken", "negative", "lowered")
    assert to_impact_summary(None, "T").thesis_verdict == "not_assessable"  # defaults


def test_to_record_reads_position_metrics():
    h = Holding(cik="1", ticker="T", owned=1, target_weight_pct=10.0, thesis="th", added_at="t")
    b = MetricsBundle()
    b.results["position_metrics"] = MetricResult(
        metric="position_metrics", status=MetricStatus.COMPUTED,
        components={"weight_pct": 8.0, "unrealized_pl_pct": 25.0},
        formula_version="position_metrics.v1", as_of="t")
    r = to_record(h, b)
    assert r.owned and r.current_weight_pct == 8.0 and r.unrealized_pl_pct == 25.0
    assert r.target_weight_pct == 10.0 and r.thesis == "th"
    assert to_record(h, MetricsBundle()).current_weight_pct is None  # no position_metrics


# ---- engine: decide → rationale → shadow, and V3 re-derivation -------------
def test_engine_owned_produces_decision_p3_shadow_and_v3_exact_match():
    repo = Repo(init_db(":memory:"))
    ext = to_extraction_summary(_p1("critical", ["going_concern"]))
    imp = to_impact_summary(None, "T")
    rec = Record(ticker="T", owned=True, thesis="th")
    metrics = MetricsBundle()
    res = _engine(repo).run(record=rec, extraction=ext, impact=imp, metrics=metrics,
                            accession_number="a", ticker="T", as_of="2025-01-01")
    assert res.decision.signal == "STRONG_REVIEW_SELL"
    assert res.decision.posture == "critical_review"
    assert res.analysis_id and res.shadow_log_id

    # shadow row round-trips ALL persisted content from the engine decision (not the LLM echo)
    sl = repo.list_shadow_log("T")[0]
    assert sl.hypothetical_signal == "STRONG_REVIEW_SELL"
    assert sl.review_posture == "critical_review"
    assert sl.price_at_eval == 42.0
    assert json.loads(sl.rules_fired_json) == res.decision.rules_fired
    assert "M1" in res.decision.rules_fired
    assert json.loads(sl.rules_skipped_json) == res.decision.rules_skipped
    assert json.loads(sl.computed_inputs_json) == []          # empty MetricsBundle

    # persisted P3 analysis output_json also carries the engine decision verbatim
    p3_row = next(a for a in repo.list_analyses("a") if a.stage == "P3")
    stored = json.loads(p3_row.output_json)
    assert stored["hypothetical_signal"] == "STRONG_REVIEW_SELL"
    assert stored["rules_fired"] == res.decision.rules_fired

    # DoD: V3 re-derives the same decision from the same inputs
    bundle = VerifyBundle(rendered_text="", metrics=metrics, decision=res.decision, record=rec,
                          extraction=ext, impact=imp, disclaimer_text=DISCLAIMER, trade_action=None)
    v3 = next(c for c in run_all(bundle).results if c.check_id == "V3")
    assert v3.verdict == "pass"


def test_engine_watch_record_is_not_applicable_and_writes_no_shadow():
    repo = Repo(init_db(":memory:"))
    res = _engine(repo).run(record=Record(ticker="W", owned=False),
                            extraction=ExtractionSummary(), impact=ImpactSummary(),
                            metrics=MetricsBundle(), accession_number="a", ticker="W", as_of="t")
    assert res.decision.signal == "NOT_APPLICABLE_WATCHLIST"
    assert res.shadow_log_id is None and res.p3 is None
    assert repo.count_shadow_log() == 0


def test_engine_shadow_log_price_at_eval_is_nullable():
    # price_at_eval is nullable by design (schema §6). Two None paths must persist NULL,
    # not crash or coerce to 0.0: (a) no provider wired, (b) no price on/before eval date.
    ext = to_extraction_summary(_p1("critical", ["going_concern"]))
    rec = Record(ticker="T", owned=True, thesis="th")

    class _NoPrice:
        def close_on_or_before(self, ticker, date_iso):
            return None

    for provider in (None, _NoPrice()):
        repo = Repo(init_db(":memory:"))
        SignalEngine(repo, FakeLLMClient(responder=lambda _s, _u: _p3_json()),
                     price_provider=provider, model_label="f", now_fn=lambda: "t").run(
            record=rec, extraction=ext, impact=ImpactSummary(), metrics=MetricsBundle(),
            accession_number="a", ticker="T", as_of="t")
        assert repo.list_shadow_log("T")[0].price_at_eval is None


def test_engine_decision_is_authoritative_over_llm_echo():
    # matrix decides STRONG_REVIEW_SELL (critical flag); the LLM echoed HOLD → ignored.
    repo = Repo(init_db(":memory:"))
    ext = to_extraction_summary(_p1("critical", ["going_concern"]))
    res = _engine(repo, _p3_json(hypothetical_signal="HOLD", review_posture="monitor",
                                 rules_fired=["M8"])).run(
        record=Record(ticker="T", owned=True, thesis="th"), extraction=ext,
        impact=ImpactSummary(), metrics=MetricsBundle(), accession_number="a",
        ticker="T", as_of="t")
    # posture/signal AND rules come from the engine (M1), never the LLM echo (HOLD/monitor/M8)
    assert res.p3.hypothetical_signal == "STRONG_REVIEW_SELL"
    assert res.p3.review_posture == "critical_review"
    assert res.p3.rules_fired == res.decision.rules_fired and "M1" in res.p3.rules_fired
    assert "M8" not in res.p3.rules_fired                       # LLM echo discarded
    # and the same is true of the persisted P3 output_json (guards the DB write path)
    stored = json.loads(next(a for a in repo.list_analyses("a") if a.stage == "P3").output_json)
    assert stored["hypothetical_signal"] == "STRONG_REVIEW_SELL"
    assert stored["rules_fired"] == res.decision.rules_fired


def _hold_record():
    return Record(ticker="T", owned=True, current_weight_pct=5.0, target_weight_pct=10.0,
                  thesis="th")


def test_valid_escalation_toward_caution_is_applied():
    repo = Repo(init_db(":memory:"))
    res = _engine(repo, _p3_json(escalation_request={"to": "TRIM", "justification": "gov"})).run(
        record=_hold_record(), extraction=ExtractionSummary(),
        impact=ImpactSummary(thesis_verdict="intact", net_direction="neutral"),
        metrics=MetricsBundle(), accession_number="a", ticker="T", as_of="t")
    assert res.escalated and res.decision.signal == "TRIM"   # HOLD → TRIM (one notch)
    assert res.p3.escalation_request.to == "TRIM"


def test_invalid_escalation_toward_aggression_is_ignored():
    repo = Repo(init_db(":memory:"))
    eng = _engine(repo, _p3_json(escalation_request={"to": "ACCUMULATE", "justification": "b"}))
    res = eng.run(
        record=_hold_record(), extraction=ExtractionSummary(),
        impact=ImpactSummary(thesis_verdict="intact", net_direction="neutral"),
        metrics=MetricsBundle(), accession_number="a", ticker="T", as_of="t")
    assert not res.escalated and res.decision.signal == "HOLD"
    assert res.p3.escalation_request is None


# ---- shadow report ---------------------------------------------------------
def test_render_shadow_report():
    rows = [SignalShadowLog(accession_number="a", ticker="T", review_posture="monitor",
                            hypothetical_signal="HOLD", rules_fired_json="[]",
                            rules_skipped_json="[]", computed_inputs_json="[]", created_at="t")]
    out = render_shadow_report(rows)
    assert "1 evaluations" in out and "monitor=1" in out and "UNVALIDATED" in out
    assert "No shadow-signal" in render_shadow_report([])
