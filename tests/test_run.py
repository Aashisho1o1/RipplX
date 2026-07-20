"""Production pipeline runner (F1): add -> ingest(seeded) -> process -> digest, offline.

Drives pipeline/run.py with a FakeLLMClient and a fixture HTML fetcher — no network — the
    same code path the CLI `process` and `analyze` commands use with real clients.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finwatch.db import Company, Filing, Repo, init_db
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

# New evidence contract: the model returns section_key + exact snippet only; the server
# anchors it to derive char_start/char_end. No offsets here on purpose.
_P1 = json.dumps({
    "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
    "classification": {"overall_severity": "medium"},
    "findings": [{"finding_id": "f1", "headline": "Services growth", "severity": "medium",
        "critical_flag": None, "evidence": [{
            "accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
            "snippet": "Net sales increased 5% year over year"}]}],
    "extraction_confidence": "high", "gaps": [],
})
def _harness_response(system, draft):
    if "finance Skeptic" in system:
        return json.dumps({"action": "done", "obligations": []})
    return json.dumps({"action": "submit", "draft": json.loads(draft)})


def _responder(system, _user):
    if "chair the investment committee" in system:
        raise AssertionError("P3 must not run in the launch pipeline")
    if "portfolio manager and risk officer" in system:
        raise AssertionError("P2 must not run in the launch pipeline")
    return _harness_response(system, _P1)


def _seed(repo, *, owned=1, url="https://www.sec.gov/x.htm"):
    repo.upsert_company(Company(cik=CIK, ticker="MSFT", name="Microsoft", sic_code="7372",
                               is_financial=0, added_at="t"))
    repo.upsert_company(Company(cik=CIK, ticker="MSFT", added_at="t"))
    repo.track_company(CIK, at="t")
    repo.upsert_filing(Filing(accession_number=ACCN, cik=CIK, form_type="10-Q",
                              filed_at="2024-08-01", primary_doc_url=url))


def _seed_other(
    repo, *, status="fetched", filed_at="2024-08-02",
    url="https://www.sec.gov/aapl.htm",
):
    cik = "0000320193"
    accession = "0000320193-24-000081"
    repo.upsert_company(Company(cik=cik, ticker="AAPL", name="Apple", sic_code="3571",
                                is_financial=0, added_at="t"))
    repo.upsert_company(Company(cik=cik, ticker="AAPL", added_at="t"))
    repo.track_company(cik, at="t")
    repo.upsert_filing(Filing(
        accession_number=accession,
        cik=cik,
        form_type="10-Q",
        filed_at=filed_at,
        primary_doc_url=url,
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
    assert {a.stage for a in repo.list_analyses(ACCN)} == {"P1", "P1_TRACE"}
    assert repo.get_filing(ACCN).status in ("verified", "analyzed")
    assert repo.get_filing(ACCN).processed_at == "t"
    md = render_digest(repo, since="2024-01-01").markdown
    assert "MSFT" in md and "Services growth" in md
    assert "Net sales increased 5% year over year" in md


def test_server_anchors_evidence_and_ignores_model_supplied_offsets():
    # The DeepSeek-Flash failure mode: the model returns a verbatim quote but a WRONG
    # character span. The server must re-anchor to the true offsets and publish — not
    # withhold — and the persisted offsets must be the server's, not the model's.
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    wrong_offsets = json.dumps({
        "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
        "classification": {"overall_severity": "medium"},
        "findings": [{"finding_id": "f1", "headline": "Services growth", "severity": "medium",
            "critical_flag": None, "evidence": [{
                "accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
                "char_start": 0, "char_end": 3,  # deliberately wrong
                "snippet": "Net sales increased 5% year over year"}]}],
        "extraction_confidence": "high", "gaps": [],
    })
    llm = FakeLLMClient(responder=lambda system, _u: _harness_response(system, wrong_offsets))
    orch = build_orchestrator(repo, llm=llm, companyfacts_provider=lambda _c: MSFT_CF,
                              model="fake/model", now_fn=lambda: "t")
    result = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")

    assert result[0].ok and not result[0].withheld  # published, not withheld
    ev = json.loads(repo.latest_analysis(ACCN, "P1").output_json)["findings"][0]["evidence"][0]
    section = next(
        s.text for s in repo.list_filing_sections(ACCN) if s.section_key == "mdna"
    )
    assert section[ev["char_start"]:ev["char_end"]] == "Net sales increased 5% year over year"
    assert (ev["char_start"], ev["char_end"]) != (0, 3)  # model offsets were overwritten


def test_non_verbatim_evidence_snippet_is_withheld():
    # A snippet that is not an exact substring of its section cannot be anchored → the
    # fresh attempt fails → nothing is published (fail-closed preserved).
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    fabricated = json.dumps({
        "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
        "classification": {"overall_severity": "medium"},
        "findings": [{"finding_id": "f1", "headline": "Fabricated claim", "severity": "medium",
            "critical_flag": None, "evidence": [{
                "accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
                "snippet": "This exact sentence never appears in the filing text at all."}]}],
        "extraction_confidence": "high", "gaps": [],
    })
    llm = FakeLLMClient(responder=lambda system, _u: _harness_response(system, fabricated))
    orch = build_orchestrator(repo, llm=llm, companyfacts_provider=lambda _c: MSFT_CF,
                              model="fake/model", now_fn=lambda: "t")
    result = process_latest(repo, orch, fetch_html=lambda _u: TENQ, now_fn=lambda: "t")

    assert result[0].ok and not result[0].withheld  # finding-local failure; metrics still publish
    assert "Fabricated claim" not in render_digest(repo, since="2024-01-01").markdown
    trace = json.loads(repo.latest_analysis(ACCN, "P1_TRACE").output_json)
    assert trace["publication_outcome"] == "metrics_only"
    assert trace["dropped_findings"][0]["error_codes"] == ["QUOTE_NOT_EXACT"]


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


def test_latest_selector_can_scope_to_a_supported_filing_type():
    repo = Repo(init_db(":memory:"))
    _seed(repo)  # 10-Q on 2024-08-01
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000071", cik=CIK, form_type="8-K",
        filed_at="2024-08-02", primary_doc_url="https://www.sec.gov/8k.htm",
    ))
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000072", cik=CIK, form_type="10-K/A",
        filed_at="2024-08-03", primary_doc_url="https://www.sec.gov/10ka.htm",
    ))

    assert newest_filing_to_analyze(repo).form_type == "10-K/A"
    assert newest_filing_to_analyze(repo, form_type="10-Q").accession_number == ACCN
    assert newest_filing_to_analyze(repo, form_type="8-K").form_type == "8-K"
    assert newest_filing_to_analyze(repo, form_type="10-K").form_type == "10-K/A"


def test_form_scoped_selector_never_falls_through_within_that_form():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000071", cik=CIK, form_type="8-K",
        filed_at="2024-08-02", primary_doc_url="https://www.sec.gov/old-8k.htm",
    ))
    repo.upsert_filing(Filing(
        accession_number="0000789019-24-000073", cik=CIK, form_type="8-K/A",
        filed_at="2024-08-03", primary_doc_url="https://www.sec.gov/new-8ka.htm",
        status="verified",
    ))

    assert newest_filing_to_analyze(repo, form_type="8-K") is None
    assert newest_filing_to_analyze(repo, form_type="10-Q").accession_number == ACCN


def test_latest_selector_rejects_an_unsupported_form_scope():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    with pytest.raises(ValueError, match="unsupported filing form"):
        newest_filing_to_analyze(repo, form_type="20-F")


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


@pytest.mark.parametrize("failure", ["missing_url", "fetch_exception"])
def test_two_failed_download_attempts_allow_another_issuer_to_run(failure):
    repo = Repo(init_db(":memory:"))
    _seed(repo)  # older eligible issuer
    other_cik, other_accession = _seed_other(
        repo,
        url=None if failure == "missing_url" else "https://www.sec.gov/aapl.htm",
    )
    fetch_calls = 0

    def fetch(_url):
        nonlocal fetch_calls
        fetch_calls += 1
        raise RuntimeError("offline test failure")

    for attempt in range(2):
        result = process_latest(
            repo, _orch(repo), fetch_html=fetch,
            now_fn=lambda attempt=attempt: f"t{attempt}",
        )
        assert len(result) == 1 and not result[0].ok
        assert result[0].accession == other_accession

    download = repo.get_filing_stage(other_accession, "download")
    assert download is not None and download.attempts == 2
    assert fetch_calls == (0 if failure == "missing_url" else 2)
    assert newest_filing_to_analyze(repo, other_cik) is None
    assert newest_filing_to_analyze(repo).accession_number == ACCN


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
    real_run_all = orchestrator_module.run_all
    verification_calls = 0

    def fail_run_all_once(bundle, *args, **kwargs):
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls == 1:
            raise RuntimeError("temporary verifier failure")
        return real_run_all(bundle, *args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "run_all", fail_run_all_once)
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
    assert "could not be analyzed — the pipeline did not complete" in md

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
    llm.responder = lambda system, _user: _harness_response(
        system, json.dumps(updated_p1)
    )
    retry = process_latest(repo, orch, fetch_html=lambda _u: updated_html, now_fn=lambda: "t2")
    assert retry[0].ok
    analyses = [a for a in repo.list_analyses(ACCN) if a.stage == "P1"]
    assert len(analyses) == 2
    assert len(llm.calls) == p1_calls + 2  # Generator + finance Skeptic
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
    assert len(llm.calls) == calls_after_retry == 0  # metrics fail before paid model work
    metrics = repo.get_filing_stage(ACCN, "metrics")
    assert metrics is not None and metrics.attempts == 2
    assert repo.get_filing_stage(ACCN, "extract") is None
    assert repo.get_filing(ACCN).status == "failed"
    assert "could not be analyzed — the pipeline did not complete" in render_digest(
        repo, since="2024-01-01"
    ).markdown


# ---- CLI guardrails (no network) -------------------------------------------
def test_cli_process_requires_model(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from finwatch.cli import app

    # Isolate from any developer .env in the repo root (load_config reads ./.env).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEC_USER_AGENT", "Test t@e.com")
    monkeypatch.setenv("FINWATCH_DB", str(tmp_path / "fw.db"))
    monkeypatch.delenv("FINWATCH_MODEL", raising=False)
    result = CliRunner().invoke(app, ["process"])
    assert result.exit_code == 1 and "model not configured" in result.output.lower()
