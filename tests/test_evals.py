"""Golden-set eval harness: recorded run, scoring, thresholds, bake-off, report."""
from __future__ import annotations

from finwatch.evals.golden import load_manifest
from finwatch.evals.harness import (
    ModelReport,
    Thresholds,
    bakeoff,
    render_report,
    run_recorded,
)


def test_golden_manifest_pins_real_accessions():
    cases = load_manifest()
    assert len(cases) == 12
    assert {c.id for c in cases} == {
        "going_concern_10k",
        "non_reliance_8k",
        "bankruptcy_acceleration_8k",
        "delisting_8k",
        "cyber_disruption_8k",
        "cyber_data_access_8k",
        "furnished_earnings_8k",
        "clean_10q",
        "executive_transition_8k",
        "ceo_appointment_8k",
        "part_three_amendment_10ka",
        "clean_annual_10k",
    }
    # real accessions (18-digit dashed form), not invented
    for c in cases:
        assert len(c.accession) == 20 and c.accession[10] == "-"
        assert c.primary_doc.startswith("https://www.sec.gov/Archives/")
        assert len(c.filed_at) == 10
    critical = [c for c in cases if c.category == "critical"]
    assert {f for c in critical for f in c.expected_critical_flags} == {
        "going_concern",
        "item_4_02_non_reliance",
        "item_1_03_bankruptcy",
        "item_2_04_acceleration",
        "item_3_01_delisting",
        "cyber_1_05_critical_tier",
    }


def test_recorded_run_meets_dod():
    # DoD: critical recall 100%, verifier pass, JSON valid, no false alarms.
    report = run_recorded()
    assert report.critical_recall == 1.0
    assert report.verifier_pass_rate == 1.0
    assert report.json_valid_rate == 1.0
    assert report.false_alarms == 0
    assert report.passes(Thresholds())


def test_per_case_scores():
    scores = {s.case_id: s for s in run_recorded().scores}
    # Critical cases extract their flag; boring/routine cases don't scream.
    assert scores["going_concern_10k"].critical_recall == 1.0
    assert scores["non_reliance_8k"].critical_recall == 1.0
    assert not scores["furnished_earnings_8k"].false_alarm
    assert not scores["clean_10q"].false_alarm
    assert not scores["part_three_amendment_10ka"].false_alarm
    assert all(s.verifier_pass for s in scores.values())


def test_bakeoff_picks_cheapest_passing():
    good_cheap = run_recorded()
    good_cheap.model = "cheap"
    for s in good_cheap.scores:
        s.cost_usd = 0.001

    good_expensive = run_recorded()
    good_expensive.model = "expensive"
    for s in good_expensive.scores:
        s.cost_usd = 0.10

    failing = run_recorded()
    failing.model = "failing"
    failing.scores[0].critical_recall = 0.0  # misses a critical flag -> disqualified

    bo = bakeoff([good_expensive, failing, good_cheap])
    assert bo.winner == "cheap"          # cheapest that clears all thresholds
    md = render_report(bo)
    assert "Winner (cheapest passing): `cheap`" in md
    assert "| failing |" in md and "✗" in md


def test_bakeoff_no_winner_when_all_fail():
    r = ModelReport(model="m", scores=run_recorded().scores)
    r.scores[0].verifier_pass = False   # break the verifier pass rate
    bo = bakeoff([r])
    assert bo.winner is None
    assert "No model cleared every threshold." in render_report(bo)


def test_critical_recall_disqualifies_missing_flag():
    r = run_recorded()
    r.scores = [s for s in r.scores if s.case_id == "going_concern_10k"]
    r.scores[0].critical_recall = 0.5   # partial recall on a critical case
    assert not r.passes(Thresholds())   # < 100% critical recall is disqualifying


def test_cli_eval_recorded_runs_without_keys():
    from typer.testing import CliRunner

    from finwatch.cli import app

    result = CliRunner().invoke(app, ["eval"])  # no --models -> recorded golden run
    assert result.exit_code == 0
    assert "Winner (cheapest passing): `recorded`" in result.output


def test_bakeoff_survives_a_case_fetch_failure():
    # A fetch failure on one case must score that case a miss, not crash the bake-off.
    from finwatch.evals.golden import load_case_html, load_manifest, load_recorded_p1
    from finwatch.evals.harness import run_model
    from finwatch.llm.router import FakeLLMClient

    cases = load_manifest()

    def html_for(c):
        if c.category == "critical":
            raise RuntimeError("simulated 404")
        return load_case_html(c.id)

    rep = run_model(
        "m",
        lambda c: FakeLLMClient(responder=lambda _s, _u, cid=c.id: load_recorded_p1(cid)),
        html_for, cases)
    assert len(rep.scores) == len(cases)   # every case scored — no crash
    crit = [s for s in rep.scores if s.category == "critical"]
    assert crit and all(not s.json_valid and s.critical_recall == 0.0 for s in crit)
    assert not rep.passes(Thresholds())    # missing a critical flag -> disqualified
