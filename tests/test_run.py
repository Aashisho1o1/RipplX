"""Production pipeline runner (F1): add -> ingest(seeded) -> process -> digest, offline.

Drives pipeline/run.py with a FakeLLMClient and a fixture HTML fetcher — no network — the
    same code path the CLI `process` and `analyze` commands use with real clients.
"""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.db import Company, Filing, Holding, Repo, init_db
from finwatch.digest import render_digest
from finwatch.llm.router import FakeLLMClient
from finwatch.pipeline.run import (
    build_orchestrator,
    newest_filing_to_analyze,
    process_filing,
    process_latest,
    process_parsing,
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
def _responder(system, _user):
    if "chair the investment committee" in system:
        raise AssertionError("P3 must not run in the launch pipeline")
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


def test_process_latest_runs_pipeline_persists_and_digest_renders():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    assert newest_filing_to_analyze(repo).accession_number == ACCN

    results = process_latest(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert len(results) == 1 and results[0].ok
    assert results[0].verdict in ("PASS", "PASS_WITH_WARNINGS")

    # every stage persisted; filing marked processed; digest now has content
    assert {a.stage for a in repo.list_analyses(ACCN)} == {"P1", "P2"}
    assert repo.count_shadow_log() == 0
    assert repo.get_filing(ACCN).status in ("verified", "analyzed")
    assert repo.get_filing(ACCN).processed_at == "t"
    md = render_digest(repo, since="2024-01-01").markdown
    assert "MSFT" in md and "Services growth supports the thesis." in md


def test_process_is_idempotent():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    orch = _orch(repo)
    process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert newest_filing_to_analyze(repo) is None
    assert process_latest(repo, orch, fetch_html=lambda _u: TENQ) == []


def test_latest_selector_excludes_newer_unsupported_sec_forms():
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
    assert newest_filing_to_analyze(repo).accession_number == ACCN


def test_latest_selector_never_falls_through_to_older_history():
    repo = Repo(init_db(":memory:"))
    _seed(repo)   # a 10-Q at 2024-08-01
    repo.upsert_filing(Filing(accession_number="0000789019-24-000081", cik=CIK, form_type="8-K",
                              filed_at="2024-08-02", primary_doc_url="https://www.sec.gov/8k.htm"))
    repo.upsert_filing(Filing(accession_number="0000789019-24-000082", cik=CIK, form_type="10-K",
                              filed_at="2024-08-03", primary_doc_url="https://www.sec.gov/10k.htm"))
    repo.upsert_filing(Filing(accession_number="0000789019-24-000083", cik=CIK, form_type="8-K/A",
                              filed_at="2024-08-04", primary_doc_url="https://www.sec.gov/8ka.htm"))

    newest = newest_filing_to_analyze(repo)
    assert newest is not None and newest.form_type == "8-K/A"
    repo.set_filing_status(newest.accession_number, "verified", processed_at="t")
    assert newest_filing_to_analyze(repo) is None
    # The three older, unprocessed filings remain deliberately inaccessible.
    assert sum(f.status not in {"verified", "analyzed"} for f in repo.list_filings(CIK)) == 3


def test_manual_review_newest_is_terminal_and_not_rebilled():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000080",
        cik=CIK,
        form_type="8-K",
        filed_at="2024-08-02",
        primary_doc_url="https://www.sec.gov/8k.htm",
        status="analyzed",
    ))

    assert newest_filing_to_analyze(repo) is None
    assert process_latest(repo, _orch(repo), fetch_html=lambda _u: TENQ) == []


def test_process_reports_errors_without_aborting():
    repo = Repo(init_db(":memory:"))
    _seed(repo, url=None)                                       # no primary-doc URL
    r = process_latest(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert len(r) == 1 and not r[0].ok and "primary-document URL" in r[0].error

    repo2 = Repo(init_db(":memory:"))
    _seed(repo2)

    def boom(_url):
        raise RuntimeError("network down")
    r2 = process_latest(repo2, _orch(repo2), fetch_html=boom, now_fn=lambda: "t")
    assert not r2[0].ok and "fetch failed" in r2[0].error
    assert repo2.get_filing(ACCN).status == "failed"


def test_transient_failure_is_retried_and_not_rendered_clean():
    # Remediation-review regression: P1 commits before P2, so a transient error in a later
    # stage must (a) mark the filing 'failed' and retry it next run — never permanently skip a
    # filing that has a P1 — and (b) NOT render the half-analyzed filing as a clean result.
    repo = Repo(init_db(":memory:"))
    _seed(repo)

    def flaky(system, _user):
        if "portfolio manager and risk officer" in system:
            raise RuntimeError("rate limited (429)")     # P2 blows up after P1 committed
        return _P1

    llm = FakeLLMClient(responder=flaky)
    orch = build_orchestrator(repo, llm_extract=llm, llm_reason=llm,
                              companyfacts_provider=lambda _c: MSFT_CF, price_provider=repo,
                              model_extract="x", model_reason="r", now_fn=lambda: "t")
    r = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert not r[0].ok and "pipeline failed" in r[0].error
    assert repo.get_filing(ACCN).status == "failed"
    assert repo.latest_analysis(ACCN, "P1") is not None       # P1 was committed
    # retried, not stranded, on the next run
    assert newest_filing_to_analyze(repo).accession_number == ACCN
    # digest flags it, does not present it as clean
    md = render_digest(repo, since="2024-01-01").markdown
    assert "manual review required" in md

    p1_calls = sum("portfolio manager" not in system and "investment committee" not in system
                   for system, _ in llm.calls)
    llm.responder = _responder
    retry = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert retry[0].ok
    assert len([a for a in repo.list_analyses(ACCN) if a.stage == "P1"]) == 1
    assert sum("portfolio manager" not in system and "investment committee" not in system
               for system, _ in llm.calls) == p1_calls


def test_parse_only_rerun_invalidates_stale_analysis_and_stops_after_p0():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    process_latest(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert repo.latest_analysis(ACCN, "P1") is not None

    result = process_parsing(
        repo, repo.get_filing(ACCN), fetch_html=lambda _u: TENQ, now_fn=lambda: "t2"
    )

    assert result.ok and result.verdict == "PARSED"
    assert repo.list_analyses(ACCN) == []
    assert repo.get_filing(ACCN).status == "sectioned"
    progress = {stage.stage: stage.status for stage in repo.list_filing_stages(ACCN)}
    assert progress["parse"] == "completed"
    assert progress["extract"] == "pending"


def test_analysis_only_rerun_reuses_sections_without_fetching_document():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    process_latest(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")

    def unexpected_fetch(_url):
        raise AssertionError("analysis-only rerun must not fetch the filing")

    result = process_filing(
        _orch(repo),
        repo,
        repo.get_filing(ACCN),
        fetch_html=unexpected_fetch,
        records=[],
        rerun_from="extract",
        now_fn=lambda: "t2",
    )

    assert result.ok
    assert repo.get_filing_stage(ACCN, "download").status == "completed"


def test_failed_parse_rerun_keeps_previous_verified_analysis():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    process_latest(repo, _orch(repo), fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    previous = repo.latest_analysis(ACCN, "P1")

    result = process_parsing(
        repo,
        repo.get_filing(ACCN),
        fetch_html=lambda _u: "<html><body>not a filing</body></html>",
        now_fn=lambda: "t2",
    )

    assert not result.ok
    assert repo.latest_analysis(ACCN, "P1").id == previous.id
    assert repo.get_filing_stage(ACCN, "parse").status == "failed"


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
