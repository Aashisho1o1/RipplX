"""Launch pipeline: P0 → P1 → starter metrics → verify end-to-end."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finwatch.db import Analysis, Company, Filing, Repo, VerificationResult, init_db
from finwatch.llm.router import FakeLLMClient
from finwatch.llm.stages import P1Extractor
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import Orchestrator
from finwatch.preprocess.preprocessor import Preprocessor
from finwatch.presentation.service import PresentationService
from finwatch.verify.checks import CheckResult, VerificationReport

FX = Path(__file__).parent / "fixtures"
TENQ = (FX / "tenq_sample.html").read_text()
MSFT_CF = json.loads((FX / "companyfacts" / "MSFT.json").read_text())
ACCN, CIK = "0000789019-24-000001", "0000789019"

P1_JSON = json.dumps({
    "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
    "classification": {"overall_severity": "medium"},
    "findings": [{"finding_id": "f1", "headline": "Revenue rose on services", "severity": "medium",
        "critical_flag": None, "evidence": [{
            "accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
            "char_start": 98, "char_end": 135,
            "snippet": "Net sales increased 5% year over year"}]}],
    "extraction_confidence": "high", "gaps": [],
})
def _respond(system, _user):
    if "finance Skeptic" in system:
        return json.dumps({"action": "done", "obligations": []})
    return json.dumps({"action": "submit", "draft": json.loads(P1_JSON)})

def _orchestrator(repo):
    llm = FakeLLMClient(responder=_respond)
    return Orchestrator(
        repo, Preprocessor(repo, now_fn=lambda: "t"),
        P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        MetricsService(repo, lambda _cik: MSFT_CF, now_fn=lambda: "t"),
        now_fn=lambda: "t",
    )


def _seed(repo):
    repo.upsert_company(Company(cik=CIK, ticker="MSFT", sic_code="7372",
                               sector_class="general", is_financial=0, added_at="t"))
    repo.upsert_filing(Filing(accession_number=ACCN, cik=CIK, form_type="10-Q",
                              filed_at="2024-08-02", period_of_report="2024-06-29"))


def test_pipeline_end_to_end_passes_and_persists():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    fa = _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01")

    assert fa.p1.classification.overall_severity == "medium"
    assert fa.metrics.get("revenue_growth").status.value == "unavailable"
    assert "current source is stale" in " ".join(
        fa.metrics.get("revenue_growth").unavailable_missing
    )
    assert fa.verification.verdict in ("PASS", "PASS_WITH_WARNINGS")
    assert not fa.withheld

    # persisted: one P1 analysis with embedded evidence; no parallel claim graph
    stages = {a.stage for a in repo.list_analyses(ACCN)}
    assert stages == {"P1", "P1_TRACE"}
    assert repo.count_verification_results(fa.p1_analysis_id) == len(fa.verification.results)
    progress = {stage.stage: stage for stage in repo.list_filing_stages(ACCN)}
    assert progress["parse"].status == "completed"
    assert '"mdna"' in progress["parse"].diagnostics_json
    assert progress["extract"].status == "completed"
    assert "signal" not in progress
    assert progress["verify"].status == "completed"

    repo.track_company(CIK, at="t")
    certificate = PresentationService(repo).certificate(ACCN)
    assert certificate is not None
    assert PresentationService(repo, user_id="untracked-user").certificate(ACCN) is None
    assert certificate.schema_version == "certificate.v2"
    assert certificate.p1_analysis_id == fa.p1_analysis_id
    assert certificate.trace_analysis_id == fa.trace_analysis_id
    assert certificate.published_finding_ids == ["f1"]
    assert certificate.evidence[0]["section_sha256"]
    assert certificate.metrics
    assert all("formula_version" in row for row in certificate.metrics)


def test_pipeline_runs_v1_v4_v5_and_v2_data_quality_without_v3():
    # V1/V4/V5 gate publication and V2 accounting identities run as data-quality.
    # V3 was removed with the P3 research code (no rule re-derivation in launch).
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    fa = _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01")
    ids = {c.check_id for c in fa.verification.results}
    assert "V1" in ids and "V4" in ids and "V5" in ids
    assert {"V2a", "V2b", "V2c"} <= ids                 # V2 audit is present now
    assert "V3" not in ids
    assert fa.verification.verdict in ("PASS", "PASS_WITH_WARNINGS")


def test_certificate_is_frozen_to_linked_attempt_not_later_database_rows():
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    fa = _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01"
    )
    repo.track_company(CIK, at="t")
    service = PresentationService(repo)
    before = service.certificate(ACCN)
    assert before is not None

    later_id = repo.insert_analysis(Analysis(
        accession_number=ACCN,
        ticker="MSFT",
        stage="P1",
        model="later",
        prompt_version="later",
        output_json='{"unrelated":"later attempt row"}',
        created_at="later",
    ))
    repo.insert_verification_results([VerificationResult(
        analysis_id=later_id,
        check_id="V1",
        verdict="fail",
        severity="blocking",
        detail="later detail must not enter the certificate",
        created_at="later",
    )])
    repo.conn.execute(
        "UPDATE filing_sections SET text = ?, text_sha256 = ? WHERE accession_number = ?",
        ("later reparsed text", "b" * 64, ACCN),
    )
    repo.conn.execute("UPDATE companies SET name = ? WHERE cik = ?", ("Later Name", CIK))
    repo.conn.commit()

    after = service.certificate(ACCN)
    assert after is not None
    assert after.model_dump_json() == before.model_dump_json()
    assert after.certificate_sha256 == before.certificate_sha256
    assert after.p1_analysis_id == fa.p1_analysis_id


def test_downstream_verifier_failure_freezes_redacted_withheld_certificate(monkeypatch):
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    monkeypatch.setattr(
        "finwatch.pipeline.orchestrator.run_all",
        lambda _bundle: VerificationReport(
            verdict="FAIL",
            results=[CheckResult(
                check_id="V5",
                verdict="fail",
                severity="blocking",
                detail="sensitive verifier detail",
            )],
        ),
    )

    fa = _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01"
    )
    assert fa.withheld
    assert repo.get_filing(ACCN).status == "analyzed"
    repo.track_company(CIK, at="t")
    certificate = PresentationService(repo).certificate(ACCN)

    assert certificate is not None
    assert certificate.schema_version == "certificate.v2"
    assert certificate.outcome == "withheld"
    assert certificate.terminal_reason == "verification_failed"
    assert certificate.published_finding_ids == []
    assert certificate.classification is None
    assert certificate.evidence == []
    assert all("arguments" not in row for row in certificate.tool_calls)
    assert all(row.detail is None for row in certificate.verification)
    assert "sensitive verifier detail" not in certificate.model_dump_json()


def test_verifier_interruption_finalizes_failed_attempt_without_certificate(monkeypatch):
    repo = Repo(init_db(":memory:"))
    _seed(repo)

    def interrupted(_bundle):
        raise RuntimeError("forced verifier interruption")

    monkeypatch.setattr("finwatch.pipeline.orchestrator.run_all", interrupted)
    with pytest.raises(RuntimeError, match="forced verifier interruption"):
        _orchestrator(repo).process_html(
            filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01"
        )

    assert repo.get_filing(ACCN).status == "failed"
    trace = json.loads(repo.latest_analysis(ACCN, "P1_TRACE").output_json)
    assert trace["terminal_reason"] == "verification_incomplete"
    repo.track_company(CIK, at="t")
    assert PresentationService(repo).certificate(ACCN) is None


@pytest.mark.parametrize(
    "corruption",
    ["v1", "malformed", "pending", "wrong_accession", "mismatched_link"],
)
def test_certificate_never_falls_back_past_invalid_latest_trace(corruption):
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01"
    )
    repo.track_company(CIK, at="t")
    prior_trace = repo.latest_analysis(ACCN, "P1_TRACE")
    payload = json.loads(prior_trace.output_json)
    payload["trace_analysis_id"] = None
    if corruption == "v1":
        payload["schema_version"] = "harness.v1"
    elif corruption == "malformed":
        payload = None
    elif corruption == "pending":
        payload["publication_outcome"] = None
        payload["terminal_reason"] = None
        payload["verification_verdict"] = None
    elif corruption == "wrong_accession":
        payload["filing_snapshot"]["accession"] = "wrong-accession"
    else:
        payload["p1_analysis_id"] = 999_999
    repo.insert_analysis(Analysis(
        accession_number=ACCN,
        ticker="MSFT",
        stage="P1_TRACE",
        model="later",
        prompt_version="later",
        output_json="{" if payload is None else json.dumps(payload),
        created_at="later",
    ))

    assert PresentationService(repo).certificate(ACCN) is None


@pytest.mark.parametrize("status", ["verified", "analyzed"])
def test_certificate_rejects_status_outcome_verdict_mismatch(monkeypatch, status):
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    if status == "analyzed":
        monkeypatch.setattr(
            "finwatch.pipeline.orchestrator.run_all",
            lambda _bundle: VerificationReport(
                verdict="FAIL",
                results=[CheckResult(
                    check_id="V5", verdict="fail", severity="blocking"
                )],
            ),
        )
    _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01"
    )
    repo.track_company(CIK, at="t")
    trace_row = repo.latest_analysis(ACCN, "P1_TRACE")
    payload = json.loads(trace_row.output_json)
    if status == "verified":
        payload["publication_outcome"] = "withheld"
        payload["verification_verdict"] = "FAIL"
    else:
        payload["publication_outcome"] = "metrics_only"
        payload["verification_verdict"] = "PASS"
    repo.conn.execute(
        "UPDATE analyses SET output_json = ? WHERE id = ?",
        (json.dumps(payload), trace_row.id),
    )
    repo.conn.commit()

    assert PresentationService(repo).certificate(ACCN) is None

def test_launch_pipeline_only_runs_p1():
    repo = Repo(init_db(":memory:"))
    _seed(repo)

    def responder(s, _u):
        if "chair the investment committee" in s:
            raise AssertionError("P3 must not run in the launch pipeline")
        if "portfolio manager and risk officer" in s:
            raise AssertionError("P2 must not run in the launch pipeline")
        return _respond(s, _u)

    llm = FakeLLMClient(responder=responder)
    orch = Orchestrator(
        repo, Preprocessor(repo, now_fn=lambda: "t"),
        P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        MetricsService(repo, lambda _c: MSFT_CF, now_fn=lambda: "t"),
        now_fn=lambda: "t")

    fa = orch.process_html(filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01")

    assert {a.stage for a in repo.list_analyses(ACCN)} == {"P1", "P1_TRACE"}
    assert all("chair the investment committee" not in system for system, _ in llm.calls)
    assert all("portfolio manager and risk officer" not in system for system, _ in llm.calls)
    assert "V3" not in {c.check_id for c in fa.verification.results}
    assert fa.verification.verdict in ("PASS", "PASS_WITH_WARNINGS")
