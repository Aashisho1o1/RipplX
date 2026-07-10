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
    process_latest,
)

FX = Path(__file__).parent / "fixtures"
TENQ = (FX / "tenq_sample.html").read_text()
MSFT_CF = json.loads((FX / "companyfacts" / "MSFT.json").read_text())
ACCN, CIK = "0000789019-24-000070", "0000789019"

_P1 = json.dumps({
    "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
    "classification": {"overall_severity": "medium"},
    "findings": [{"headline": "Services growth", "severity": "medium",
        "critical_flag": None, "evidence": [{
            "accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
            "char_start": 98, "char_end": 135,
            "snippet": "Net sales increased 5% year over year"}]}],
    "extraction_confidence": "high", "gaps": [],
})
def _responder(system, _user):
    if "chair the investment committee" in system:
        raise AssertionError("P3 must not run in the launch pipeline")
    if "portfolio manager and risk officer" in system:
        raise AssertionError("P2 must not run in the launch pipeline")
    return _P1


def _seed(repo, *, owned=1, url="https://www.sec.gov/x.htm"):
    repo.upsert_company(Company(cik=CIK, ticker="MSFT", name="Microsoft", sic_code="7372",
                               is_financial=0, added_at="t"))
    repo.upsert_holding(Holding(cik=CIK, ticker="MSFT", owned=owned, added_at="t"))
    repo.upsert_filing(Filing(accession_number=ACCN, cik=CIK, form_type="10-Q",
                              filed_at="2024-08-01", primary_doc_url=url))


def _seed_other(repo, *, status="fetched", filed_at="2024-08-02"):
    cik = "0000320193"
    accession = "0000320193-24-000081"
    repo.upsert_company(Company(cik=cik, ticker="AAPL", name="Apple", sic_code="3571",
                                is_financial=0, added_at="t"))
    repo.upsert_holding(Holding(cik=cik, ticker="AAPL", owned=0, added_at="t"))
    repo.upsert_filing(Filing(
        accession_number=accession,
        cik=cik,
        form_type="10-Q",
        filed_at=filed_at,
        primary_doc_url="https://www.sec.gov/aapl.htm",
        status=status,
    ))
    return cik, accession


def _orch(repo):
    llm = FakeLLMClient(responder=_responder)
    return build_orchestrator(
        repo, llm=llm, companyfacts_provider=lambda _c: MSFT_CF,
        model="fake/model",
        now_fn=lambda: "t")


def test_process_latest_runs_pipeline_persists_and_digest_renders():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    assert newest_filing_to_analyze(repo).accession_number == ACCN

    orchestrator = _orch(repo)
    results = process_latest(
        repo, orchestrator, fetch_html=lambda _u: TENQ, now_fn=lambda: "t"
    )
    assert len(results) == 1 and results[0].ok
    assert results[0].verdict in ("PASS", "PASS_WITH_WARNINGS")

    # every stage persisted; filing marked processed; digest now has content
    assert {a.stage for a in repo.list_analyses(ACCN)} == {"P1"}
    assert repo.count_shadow_log() == 0
    assert repo.get_filing(ACCN).status in ("verified", "analyzed")
    assert repo.get_filing(ACCN).processed_at == "t"
    md = render_digest(repo, since="2024-01-01").markdown
    assert "MSFT" in md and "Services growth" in md
    assert "Net sales increased 5% year over year" in md


def test_extraction_prompt_never_includes_legacy_portfolio_accounting_or_thesis():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    repo.conn.execute(
        "UPDATE holdings SET shares = 12345, cost_basis = 678.90, thesis = ? WHERE cik = ?",
        ("private launch thesis", CIK),
    )
    repo.conn.commit()
    orch = _orch(repo)
    process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    prompts = "\n".join(user for _system, user in orch.p1.llm.calls)
    assert "12345" not in prompts
    assert "678.90" not in prompts
    assert "private launch thesis" not in prompts


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


def test_portfolio_selector_chooses_newest_eligible_per_cik_candidate():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    _other_cik, other_accession = _seed_other(repo)

    assert newest_filing_to_analyze(repo).accession_number == other_accession


def test_terminal_global_newest_does_not_starve_another_tracked_cik():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    other_cik, _other_accession = _seed_other(repo, status="verified")

    selected = newest_filing_to_analyze(repo)

    assert selected is not None and selected.accession_number == ACCN
    assert newest_filing_to_analyze(repo, other_cik) is None


def test_exhausted_global_newest_does_not_starve_another_tracked_cik():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    other_cik, other_accession = _seed_other(repo, status="failed")
    for attempt in ("one", "two"):
        repo.set_filing_stage(other_accession, "extract", "running", at=attempt)
        repo.set_filing_stage(other_accession, "extract", "failed", at=attempt)

    selected = newest_filing_to_analyze(repo)

    assert selected is not None and selected.accession_number == ACCN
    assert newest_filing_to_analyze(repo, other_cik) is None


def test_portfolio_selector_ignores_untracked_issuer_filings():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    repo.upsert_company(Company(cik="0000000002", ticker="ZZZ", added_at="t"))
    repo.upsert_filing(Filing(
        accession_number="0000000002-24-000001",
        cik="0000000002",
        form_type="8-K",
        filed_at="2024-08-10",
        primary_doc_url="https://www.sec.gov/untracked.htm",
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
    assert not r2[0].ok and r2[0].error == "filing download failed"
    assert repo2.get_filing(ACCN).status == "failed"


def test_provider_exception_text_is_never_persisted_or_returned():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    sentinel = "sk-live-provider-secret-in-exception"

    def explode(_system, _user):
        raise RuntimeError(f"authorization failed for {sentinel}")

    orchestrator = build_orchestrator(
        repo,
        llm=FakeLLMClient(responder=explode),
        companyfacts_provider=lambda _cik: MSFT_CF,
        model="fake/model",
        now_fn=lambda: "t",
    )
    result = process_latest(
        repo, orchestrator, fetch_html=lambda _url: TENQ, now_fn=lambda: "t"
    )[0]

    persisted = "\n".join(
        f"{stage.error or ''}\n{stage.diagnostics_json}"
        for stage in repo.list_filing_stages(ACCN)
    )
    assert result.error == "analysis pipeline failed"
    assert sentinel not in result.error
    assert sentinel not in persisted


def test_transient_verify_failure_retries_one_fresh_complete_attempt(monkeypatch):
    # A failure after metrics completed must not let the retry combine the old P1/metrics
    # with newly parsed sections. The second click reruns and persists every artifact.
    import finwatch.pipeline.orchestrator as orchestrator_module

    repo = Repo(init_db(":memory:"))
    _seed(repo)

    llm = FakeLLMClient(responder=_responder)
    orch = build_orchestrator(repo, llm=llm,
                              companyfacts_provider=lambda _cik: MSFT_CF,
                              model="fake/model", now_fn=lambda: "t")
    real_verify = orchestrator_module.run_with_regeneration
    verification_calls = 0

    def fail_verify_once(bundle, regenerate):
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls == 1:
            raise RuntimeError("temporary verifier failure")
        return real_verify(bundle, regenerate)

    monkeypatch.setattr(orchestrator_module, "run_with_regeneration", fail_verify_once)
    r = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")
    assert not r[0].ok and r[0].error == "analysis pipeline failed"
    assert repo.get_filing(ACCN).status == "failed"
    assert repo.latest_analysis(ACCN, "P1") is not None       # P1 was committed
    first_computation_count = len(repo.list_computations("MSFT"))
    assert first_computation_count > 0
    # retried, not stranded, on the next run
    assert newest_filing_to_analyze(repo).accession_number == ACCN
    # digest flags it, does not present it as clean
    md = render_digest(repo, since="2024-01-01").markdown
    assert "withheld pending manual review" in md

    p1_calls = len(llm.calls)
    updated_html = TENQ.replace(
        "Net sales increased 5% year over year",
        "Net sales decreased 7% year over year",
    )
    updated_p1 = json.loads(_P1)
    updated_p1["findings"][0]["headline"] = "Services revenue declined"
    updated_p1["findings"][0]["evidence"][0]["snippet"] = (
        "Net sales decreased 7% year over year"
    )
    llm.responder = lambda _system, _user: json.dumps(updated_p1)
    retry = process_latest(repo, orch, fetch_html=lambda _u: updated_html, now_fn=lambda: "t2")
    assert retry[0].ok
    analyses = [a for a in repo.list_analyses(ACCN) if a.stage == "P1"]
    assert len(analyses) == 2
    assert len(llm.calls) == p1_calls + 1
    assert len(repo.list_computations("MSFT")) == first_computation_count * 2
    latest = json.loads(repo.latest_analysis(ACCN, "P1").output_json)
    assert latest["findings"][0]["evidence"][0]["snippet"] == (
        "Net sales decreased 7% year over year"
    )
    sections = {s.section_key: s.text for s in repo.list_filing_sections(ACCN)}
    assert "Net sales decreased 7% year over year" in sections["mdna"]
    md = render_digest(repo, since="2024-01-01").markdown
    assert "Services revenue declined" in md
    assert "Net sales increased 5% year over year" not in md


def test_failed_filing_stops_after_one_full_retry_and_third_click_is_free():
    repo = Repo(init_db(":memory:"))
    _seed(repo)

    def unavailable_companyfacts(_cik):
        raise RuntimeError("companyfacts unavailable")

    llm = FakeLLMClient(responder=_responder)
    orch = build_orchestrator(
        repo,
        llm=llm,
        companyfacts_provider=unavailable_companyfacts,
        model="fake/model",
        now_fn=lambda: "t",
    )

    first = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t1")
    second = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t2")
    calls_after_retry = len(llm.calls)
    third = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t3")

    assert not first[0].ok and not second[0].ok
    assert third == []
    assert len(llm.calls) == calls_after_retry == 2
    extract = repo.get_filing_stage(ACCN, "extract")
    assert extract is not None and extract.attempts == 2
    assert repo.get_filing(ACCN).status == "failed"
    assert "withheld pending manual review" in render_digest(
        repo, since="2024-01-01"
    ).markdown


# ---- CLI guardrails (no network) -------------------------------------------
def test_cli_process_requires_model(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from finwatch.cli import app

    monkeypatch.setenv("SEC_USER_AGENT", "Test t@e.com")
    monkeypatch.setenv("FINWATCH_DB", str(tmp_path / "fw.db"))
    monkeypatch.delenv("FINWATCH_MODEL", raising=False)
    result = CliRunner().invoke(app, ["process"])
    assert result.exit_code == 1 and "model not configured" in result.output.lower()
