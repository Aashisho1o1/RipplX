"""Pipeline orchestrator: P0 → P1 → metrics → P2 → verify end-to-end (fake LLM)."""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.db import Company, Filing, Repo, init_db
from finwatch.llm.router import FakeLLMClient
from finwatch.llm.stages import P1Extractor, P2Explainer
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import Orchestrator, p2_gate
from finwatch.preprocess.preprocessor import Preprocessor

FX = Path(__file__).parent / "fixtures"
TENQ = (FX / "tenq_sample.html").read_text()
MSFT_CF = json.loads((FX / "companyfacts" / "MSFT.json").read_text())
ACCN, CIK = "0000789019-24-000001", "0000789019"

P1_JSON = json.dumps({
    "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
    "classification": {"items_8k": [], "overall_severity": "medium"},
    "claims": [{"claim_id": "c_0001", "claim_type": "evidence", "text": "rev up",
        "confidence": "high",
        "provenance": {"accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
            "char_start": 0, "char_end": 250, "text_sha256_prefix": "x",
            "snippet": "Net sales increased 5% year over year"}}],
    "material_items": [{"headline": "Revenue rose on services", "event_type": "results",
        "severity": "medium", "claim_ids": ["c_0001"]}],
    "guidance_direction": {"value": "maintained", "claim_id": None},
    "red_flags": [], "extraction_confidence": "high", "gaps": [],
})
P2_JSON = json.dumps({
    "accession_number": ACCN,
    "records_affected": [{"ticker": "MSFT", "owned": True, "impact_class": "direct",
        "channels": {"C1": {"direction": "positive"}, "C8_driver_type": "idiosyncratic"},
        "guidance_direction": "maintained", "liquidity_read": "stable",
        "net_direction": "positive", "thesis_check": {"verdict": "intact"},
        "net_read": {"text": "Services growth supports the thesis."},
        "confidence": "medium"}],
    "claims": [], "portfolio_level_notes": None,
})


class _FakePrice:
    def close_on_or_before(self, ticker, date_iso):
        return 450.0


def _orchestrator(repo):
    llm = FakeLLMClient(
        responder=lambda s, _u: P2_JSON if "portfolio manager and risk officer" in s else P1_JSON)
    return Orchestrator(
        repo, Preprocessor(repo, now_fn=lambda: "t"),
        P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        P2Explainer(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        MetricsService(repo, _FakePrice(), lambda _cik: MSFT_CF, now_fn=lambda: "t"),
        companyfacts_provider=lambda _cik: MSFT_CF, now_fn=lambda: "t",
    )


def _seed(repo):
    repo.upsert_company(Company(cik=CIK, ticker="MSFT", sic_code="7372",
                               sector_class="general", is_financial=0, added_at="t"))
    repo.upsert_filing(Filing(accession_number=ACCN, cik=CIK, form_type="10-Q",
                              filed_at="2024-08-02", period_of_report="2024-06-29"))


def test_pipeline_end_to_end_passes_and_persists():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    records = [{"ticker": "MSFT", "owned": True, "shares": 100, "cost_basis": 300.0,
                "thesis": "cloud"}]
    fa = _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01", records=records)

    assert fa.p1.classification.overall_severity == "medium"
    assert fa.metrics.get("revenue_growth").status.value == "computed"
    assert fa.p2 is not None
    assert fa.verification.verdict in ("PASS", "PASS_WITH_WARNINGS")
    assert not fa.manual_review

    # persisted: P1 + P2 analyses, claim graph, verification results
    stages = {a.stage for a in repo.list_analyses(ACCN)}
    assert stages == {"P1", "P2"}
    assert repo.count_verification_results(fa.p1_analysis_id) == len(fa.verification.results)
    assert len(repo.list_analysis_claims(fa.p1_analysis_id)) == 1


def test_pipeline_runs_v2_data_quality_and_skips_v3_without_decision():
    # V2 accounting identities now run as a data-quality audit (F10); V3 is skipped when
    # there is no P3 decision (no owned holding persisted here).
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    fa = _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01",
        records=[{"ticker": "MSFT", "owned": True, "shares": 100, "cost_basis": 300.0}])
    ids = {c.check_id for c in fa.verification.results}
    assert "V1" in ids and "V4" in ids and "V5" in ids
    assert {"V2a", "V2b", "V2c"} <= ids                 # V2 audit is present now
    v3 = next(c for c in fa.verification.results if c.check_id == "V3")
    assert v3.verdict == "skipped_not_applicable"
    assert fa.verification.verdict in ("PASS", "PASS_WITH_WARNINGS")


def test_p2_gate_on_severity_and_red_flags():
    from finwatch.llm.schemas import Classification, GuidanceDirection, P1Output, RedFlag

    def p1(sev, flags):
        return P1Output(accession_number="a", ticker="T", form_type="8-K",
                        classification=Classification(overall_severity=sev),
                        guidance_direction=GuidanceDirection(value="none_stated"),
                        red_flags=flags, extraction_confidence="high")

    assert p2_gate(p1("medium", []))
    assert p2_gate(p1("low", [RedFlag(flag="x", severity="high")]))  # flag overrides low severity
    assert not p2_gate(p1("low", []))
    assert not p2_gate(p1("routine", []))


_P3_JSON = json.dumps({
    "ticker": "MSFT", "accession_number": ACCN, "review_posture": "monitor",
    "trade_action": None, "hypothetical_signal": "HOLD", "rules_fired": [],
    "rules_skipped": [], "computed_inputs": [], "rationale": "No material change.",
    "counter_evidence": "Valuation is elevated.", "what_would_change_this": ["A guidance cut."],
    "confidence": "medium",
    "disclaimer": ("Educational analysis of public information for the portfolio owner's "
                   "own decision-making. Not individualized investment advice. "
                   "Data may be incomplete or delayed."),
})


def test_pipeline_runs_p3_v3_and_shadow_log_for_owned_record():
    from finwatch.db import Holding
    from finwatch.signals.engine import SignalEngine

    repo = Repo(init_db(":memory:"))
    _seed(repo)
    repo.upsert_holding(Holding(cik=CIK, ticker="MSFT", owned=1, shares=100, cost_basis=300.0,
                                target_weight_pct=10.0, thesis="cloud", added_at="t"))

    def responder(s, _u):
        if "chair the investment committee" in s:
            return _P3_JSON
        if "portfolio manager and risk officer" in s:
            return P2_JSON
        return P1_JSON

    llm = FakeLLMClient(responder=responder)
    eng = SignalEngine(repo, llm, price_provider=_FakePrice(), model_label="fake/m",
                       now_fn=lambda: "t")
    orch = Orchestrator(
        repo, Preprocessor(repo, now_fn=lambda: "t"),
        P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        P2Explainer(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        MetricsService(repo, _FakePrice(), lambda _c: MSFT_CF, now_fn=lambda: "t"),
        companyfacts_provider=lambda _c: MSFT_CF, signal_engine=eng, now_fn=lambda: "t")

    records = [{"ticker": "MSFT", "owned": True, "shares": 100, "cost_basis": 300.0,
                "target_weight_pct": 10.0, "thesis": "cloud"}]
    fa = orch.process_html(filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01",
                           records=records)

    assert fa.signal is not None
    assert {a.stage for a in repo.list_analyses(ACCN)} == {"P1", "P2", "P3"}
    assert repo.count_shadow_log() == 1
    v3 = next(c for c in fa.verification.results if c.check_id == "V3")
    assert v3.verdict == "pass"                              # DoD: V3 exact-match
    assert fa.verification.verdict in ("PASS", "PASS_WITH_WARNINGS")


def test_malicious_p3_prose_fails_verification_and_digest_withholds_it():
    # F2: P3 rationale/counter-evidence/what-would-change now flow through the verifier;
    # forbidden vocab / price-target prose → blocking V5 fail → digest never shows it.
    from finwatch.db import Holding
    from finwatch.digest import render_digest
    from finwatch.signals.engine import SignalEngine

    repo = Repo(init_db(":memory:"))
    _seed(repo)
    repo.upsert_holding(Holding(cik=CIK, ticker="MSFT", owned=1, shares=100, cost_basis=300.0,
                                target_weight_pct=10.0, thesis="cloud", added_at="t"))
    evil = json.loads(_P3_JSON)
    evil["rationale"] = "Guaranteed profit — our price target of $999 means you should buy now."
    evil_json = json.dumps(evil)

    def responder(s, _u):
        if "chair the investment committee" in s:
            return evil_json
        if "portfolio manager and risk officer" in s:
            return P2_JSON
        return P1_JSON

    llm = FakeLLMClient(responder=responder)
    eng = SignalEngine(repo, llm, price_provider=_FakePrice(), model_label="fake/m",
                       now_fn=lambda: "t")
    orch = Orchestrator(
        repo, Preprocessor(repo, now_fn=lambda: "t"),
        P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        P2Explainer(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        MetricsService(repo, _FakePrice(), lambda _c: MSFT_CF, now_fn=lambda: "t"),
        companyfacts_provider=lambda _c: MSFT_CF, signal_engine=eng, now_fn=lambda: "t")
    records = [{"ticker": "MSFT", "owned": True, "shares": 100, "cost_basis": 300.0,
                "target_weight_pct": 10.0, "thesis": "cloud"}]
    fa = orch.process_html(filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01",
                           records=records)

    assert fa.manual_review                                  # V5 caught it
    assert sum("chair the investment committee" in system for system, _ in llm.calls) == 2
    assert any(c.check_id == "V5" and c.verdict == "fail" for c in fa.verification.results)
    md = render_digest(repo, since="2024-01-01", include_signals=True).markdown
    assert "Guaranteed profit" not in md and "$999" not in md
    assert "rationale withheld" in md


def test_cli_shadow_report(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from finwatch.cli import app
    from finwatch.db import Repo, SignalShadowLog, init_db

    db = tmp_path / "fw.db"
    conn = init_db(str(db))
    Repo(conn).insert_shadow_log(SignalShadowLog(
        accession_number="a-1", ticker="MSFT", review_posture="risk_review",
        hypothetical_signal="TRIM", rules_fired_json="[]", rules_skipped_json="[]",
        computed_inputs_json="[]", created_at="t"))
    conn.close()

    monkeypatch.setenv("SEC_USER_AGENT", "Test t@e.com")
    monkeypatch.setenv("FINWATCH_DB", str(db))
    result = CliRunner().invoke(app, ["shadow", "report"])
    assert result.exit_code == 0
    assert "risk_review=1" in result.output and "UNVALIDATED" in result.output
