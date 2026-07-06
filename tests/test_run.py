"""Production pipeline runner (F1): add -> ingest(seeded) -> process -> digest, offline.

Drives pipeline/run.py with a FakeLLMClient and a fixture HTML fetcher — no network — the
same code path the CLI `process`/`analyze`/`verify` commands use with real clients.
"""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.db import Company, Filing, Holding, Repo, init_db
from finwatch.digest import render_digest
from finwatch.llm.router import FakeLLMClient
from finwatch.pipeline.run import (
    build_orchestrator,
    process_tracked,
    reverify,
    unanalyzed_filings,
)

FX = Path(__file__).parent / "fixtures"
TENQ = (FX / "tenq_sample.html").read_text()
MSFT_CF = json.loads((FX / "companyfacts" / "MSFT.json").read_text())
ACCN, CIK = "0000789019-24-000070", "0000789019"

_P1 = json.dumps({
    "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
    "classification": {"items_8k": [], "overall_severity": "medium"},
    "claims": [{"claim_id": "c_0001", "claim_type": "evidence", "text": "rev up",
        "confidence": "high",
        "provenance": {"accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
            "char_start": 0, "char_end": 250, "text_sha256_prefix": "x",
            "snippet": "Net sales increased 5% year over year"}}],
    "material_items": [{"headline": "Services growth", "event_type": "results",
        "severity": "medium", "claim_ids": ["c_0001"]}],
    "guidance_direction": {"value": "maintained", "claim_id": None},
    "red_flags": [], "extraction_confidence": "high", "gaps": [],
})
_P2 = json.dumps({
    "accession_number": ACCN,
    "records_affected": [{"ticker": "MSFT", "owned": True, "impact_class": "direct",
        "channels": {"C1": {"direction": "positive"}, "C8_driver_type": "idiosyncratic"},
        "guidance_direction": "maintained", "liquidity_read": "stable",
        "net_direction": "positive", "thesis_check": {"verdict": "intact"},
        "net_read": {"text": "Services growth supports the thesis."}, "confidence": "medium"}],
    "claims": [], "portfolio_level_notes": None,
})
_P3 = json.dumps({
    "ticker": "MSFT", "accession_number": ACCN, "review_posture": "monitor",
    "trade_action": None, "hypothetical_signal": "HOLD", "rules_fired": [],
    "rules_skipped": [], "computed_inputs": [], "rationale": "No material change this quarter.",
    "counter_evidence": "Valuation is elevated.", "what_would_change_this": ["A guidance cut."],
    "confidence": "medium",
    "disclaimer": ("Educational analysis of public information for the portfolio owner's "
                   "own decision-making. Not individualized investment advice. "
                   "Data may be incomplete or delayed."),
})


def _responder(system, _user):
    if "chair the investment committee" in system:
        return _P3
    if "portfolio manager and risk officer" in system:
        return _P2
    return _P1


def _seed(repo, *, owned=1, url="https://www.sec.gov/x.htm"):
    repo.upsert_company(Company(cik=CIK, ticker="MSFT", name="Microsoft", sic_code="7372",
                               is_financial=0, added_at="t"))
    repo.upsert_holding(Holding(cik=CIK, ticker="MSFT", owned=owned, shares=100,
                                cost_basis=300.0, target_weight_pct=10.0, thesis="cloud",
                                added_at="t"))
    repo.upsert_filing(Filing(accession_number=ACCN, cik=CIK, form_type="10-Q",
                              filed_at="2024-08-01", primary_doc_url=url))


def _orch(repo):
    llm = FakeLLMClient(responder=_responder)
    return build_orchestrator(
        repo, llm_extract=llm, llm_reason=llm, companyfacts_provider=lambda _c: MSFT_CF,
        price_provider=repo, model_extract="fake/x", model_reason="fake/r",
        now_fn=lambda: "t")


def test_process_tracked_runs_pipeline_persists_and_digest_renders():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    assert [f.accession_number for f in unanalyzed_filings(repo)] == [ACCN]

    results = process_tracked(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert len(results) == 1 and results[0].ok
    assert results[0].verdict in ("PASS", "PASS_WITH_WARNINGS")

    # every stage persisted; filing marked processed; digest now has content
    assert {a.stage for a in repo.list_analyses(ACCN)} == {"P1", "P2", "P3"}
    assert repo.get_filing(ACCN).status in ("verified", "analyzed")
    assert repo.get_filing(ACCN).processed_at == "t"
    md = render_digest(repo, since="2024-01-01").markdown
    assert "MSFT" in md and "Services growth supports the thesis." in md


def test_process_is_idempotent():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    orch = _orch(repo)
    process_tracked(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert unanalyzed_filings(repo) == []                      # already has a P1
    assert process_tracked(repo, orch, fetch_html=lambda _u: TENQ) == []   # nothing to redo


def test_analysis_queue_excludes_unsupported_sec_forms():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000071", cik=CIK, form_type="4",
        filed_at="2024-08-02", primary_doc_url="https://www.sec.gov/form4.htm",
    ))
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000072", cik=CIK, form_type="20-F",
        filed_at="2024-08-03", primary_doc_url="https://www.sec.gov/20f.htm",
    ))
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000073", cik=CIK, form_type="8-K/A",
        filed_at="2024-08-04", primary_doc_url="https://www.sec.gov/8ka.htm",
    ))

    assert [filing.form_type for filing in unanalyzed_filings(repo)] == ["10-Q", "8-K/A"]


def test_process_reports_errors_without_aborting():
    repo = Repo(init_db(":memory:"))
    _seed(repo, url=None)                                       # no primary-doc URL
    r = process_tracked(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert len(r) == 1 and not r[0].ok and "primary-document URL" in r[0].error

    repo2 = Repo(init_db(":memory:"))
    _seed(repo2)

    def boom(_url):
        raise RuntimeError("network down")
    r2 = process_tracked(repo2, _orch(repo2), fetch_html=boom, now_fn=lambda: "t")
    assert not r2[0].ok and "fetch failed" in r2[0].error
    assert repo2.get_filing(ACCN).status == "failed"


def test_transient_failure_is_retried_and_not_rendered_clean():
    # Remediation-review regression: P1 commits before P2/P3, so a transient error in a later
    # stage must (a) mark the filing 'failed' and retry it next run — never permanently skip a
    # filing that has a P1 — and (b) NOT render the half-analyzed filing as a clean result.
    repo = Repo(init_db(":memory:"))
    _seed(repo)

    def flaky(system, _user):
        if "portfolio manager and risk officer" in system:
            raise RuntimeError("rate limited (429)")     # P2 blows up after P1 committed
        if "chair the investment committee" in system:
            return _P3
        return _P1

    llm = FakeLLMClient(responder=flaky)
    orch = build_orchestrator(repo, llm_extract=llm, llm_reason=llm,
                              companyfacts_provider=lambda _c: MSFT_CF, price_provider=repo,
                              model_extract="x", model_reason="r", now_fn=lambda: "t")
    r = process_tracked(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert not r[0].ok and "pipeline failed" in r[0].error
    assert repo.get_filing(ACCN).status == "failed"
    assert repo.latest_analysis(ACCN, "P1") is not None       # P1 was committed
    # retried, not stranded, on the next run
    assert [f.accession_number for f in unanalyzed_filings(repo)] == [ACCN]
    # digest flags it, does not present it as clean
    md = render_digest(repo, since="2024-01-01").markdown
    assert "manual review required" in md


def test_reverify_reruns_from_db_offline():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    process_tracked(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    report = reverify(repo, ACCN, created_at="t")
    assert report is not None
    assert report.verdict in ("PASS", "PASS_WITH_WARNINGS")
    ids = {c.check_id for c in report.results}
    assert {"V1", "V4", "V5"} <= ids
    assert reverify(repo, "0000000000-00-000000") is None      # unknown accession


# ---- CLI guardrails (no network) -------------------------------------------
def test_cli_process_requires_models(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from finwatch.cli import app

    monkeypatch.setenv("SEC_USER_AGENT", "Test t@e.com")
    monkeypatch.setenv("FINWATCH_DB", str(tmp_path / "fw.db"))
    monkeypatch.delenv("FINWATCH_MODEL_EXTRACT", raising=False)
    monkeypatch.delenv("FINWATCH_MODEL_REASON", raising=False)
    result = CliRunner().invoke(app, ["process"])
    assert result.exit_code == 1 and "models not configured" in result.output.lower()


def test_cli_verify_missing_analysis(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from finwatch.cli import app

    monkeypatch.setenv("SEC_USER_AGENT", "Test t@e.com")
    monkeypatch.setenv("FINWATCH_DB", str(tmp_path / "fw.db"))
    result = CliRunner().invoke(app, ["verify", "0000000000-00-000000"])
    assert result.exit_code == 1 and "No stored analysis" in result.output
