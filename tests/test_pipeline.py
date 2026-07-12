"""Launch pipeline: P0 → P1 → starter metrics → verify end-to-end."""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.db import Company, Filing, Repo, init_db
from finwatch.llm.router import FakeLLMClient
from finwatch.llm.stages import P1Extractor
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import Orchestrator
from finwatch.preprocess.preprocessor import Preprocessor

FX = Path(__file__).parent / "fixtures"
TENQ = (FX / "tenq_sample.html").read_text()
MSFT_CF = json.loads((FX / "companyfacts" / "MSFT.json").read_text())
ACCN, CIK = "0000789019-24-000001", "0000789019"

P1_JSON = json.dumps({
    "accession_number": ACCN, "ticker": "MSFT", "form_type": "10-Q",
    "classification": {"overall_severity": "medium"},
    "findings": [{"headline": "Revenue rose on services", "severity": "medium",
        "critical_flag": None, "evidence": [{
            "accession_number": ACCN, "form_type": "10-Q", "section_key": "mdna",
            "char_start": 98, "char_end": 135,
            "snippet": "Net sales increased 5% year over year"}]}],
    "extraction_confidence": "high", "gaps": [],
})
def _orchestrator(repo):
    llm = FakeLLMClient(responder=lambda _s, _u: P1_JSON)
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
    assert stages == {"P1"}
    assert repo.count_verification_results(fa.p1_analysis_id) == len(fa.verification.results)
    progress = {stage.stage: stage for stage in repo.list_filing_stages(ACCN)}
    assert progress["parse"].status == "completed"
    assert '"mdna"' in progress["parse"].diagnostics_json
    assert progress["extract"].status == "completed"
    assert "signal" not in progress
    assert progress["verify"].status == "completed"


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

def test_launch_pipeline_only_runs_p1():
    repo = Repo(init_db(":memory:"))
    _seed(repo)

    def responder(s, _u):
        if "chair the investment committee" in s:
            raise AssertionError("P3 must not run in the launch pipeline")
        if "portfolio manager and risk officer" in s:
            raise AssertionError("P2 must not run in the launch pipeline")
        return P1_JSON

    llm = FakeLLMClient(responder=responder)
    orch = Orchestrator(
        repo, Preprocessor(repo, now_fn=lambda: "t"),
        P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t"),
        MetricsService(repo, lambda _c: MSFT_CF, now_fn=lambda: "t"),
        now_fn=lambda: "t")

    fa = orch.process_html(filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01")

    assert {a.stage for a in repo.list_analyses(ACCN)} == {"P1"}
    assert all("chair the investment committee" not in system for system, _ in llm.calls)
    assert all("portfolio manager and risk officer" not in system for system, _ in llm.calls)
    assert "V3" not in {c.check_id for c in fa.verification.results}
    assert fa.verification.verdict in ("PASS", "PASS_WITH_WARNINGS")
