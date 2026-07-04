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


def test_pipeline_verify_skips_v2_and_v3():
    # V2 (data identities) and V3 (P3 decision) are not part of the per-filing LLM gate.
    repo = Repo(init_db(":memory:"))
    _seed(repo)
    fa = _orchestrator(repo).process_html(
        filing=repo.get_filing(ACCN), html=TENQ, as_of="2025-05-01",
        records=[{"ticker": "MSFT", "owned": True, "shares": 100, "cost_basis": 300.0}])
    ids = {c.check_id for c in fa.verification.results}
    assert "V1" in ids and "V4" in ids and "V5" in ids
    assert not any(i.startswith("V2") for i in ids)  # store/sector not passed
    v3 = next(c for c in fa.verification.results if c.check_id == "V3")
    assert v3.verdict == "skipped_not_applicable"


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
