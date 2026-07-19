"""Bounded tool harness, per-finding compiler, and rounding-aware direction tests."""
from __future__ import annotations

import json

import pytest

from finwatch.core.types import MetricStatus, sector_from_sic
from finwatch.db import Company, Filing, Repo, init_db
from finwatch.llm.router import FakeLLMClient
from finwatch.llm.stages import P1Extractor, StageError
from finwatch.metrics.envelope import MetricResult, MetricsBundle
from finwatch.metrics.formulas import revenue_growth, xbrl_rounding_slack
from finwatch.presentation.service import PresentationService
from finwatch.xbrl.normalize import Fact, FactStore

META = {
    "accession_number": "0000000001-24-000001", "ticker": "TEST",
    "form_type": "10-Q", "cik": "0000000001", "filed_at": "2025-02-01",
}
SECTIONS = {"mdna": {"text": "Revenue increased while costs remained stable."}}


def _draft(*findings, form_type="10-Q"):
    severity = "routine"
    if findings:
        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        severity = min((row["severity"] for row in findings), key=rank.__getitem__)
    return {
        "accession_number": META["accession_number"], "ticker": "TEST",
        "form_type": form_type,
        "classification": {"overall_severity": severity},
        "findings": list(findings), "extraction_confidence": "high", "gaps": [],
    }


def _finding(fid, snippet, **updates):
    row = {
        "finding_id": fid, "headline": "Revenue increased", "severity": "medium",
        "critical_flag": None, "metric_id": None, "direction": None,
        "evidence": [{
            "accession_number": META["accession_number"], "form_type": "10-Q",
            "section_key": "mdna", "snippet": snippet,
        }],
    }
    row.update(updates)
    return row


def _submit(draft):
    return json.dumps({"action": "submit", "draft": draft})


def _done(obligations=None):
    return json.dumps({"action": "done", "obligations": obligations or []})


def test_generator_uses_tool_then_submits_and_skeptic_finishes():
    repo = Repo(init_db(":memory:"))
    llm = FakeLLMClient(responses=[
        json.dumps({
            "action": "tool", "tool": "search_sections",
            "arguments": {"scope": "current", "queries": ["Revenue"]},
        }),
        _submit(_draft(_finding("f1", "Revenue increased"))),
        _done(),
    ])

    output, _, _ = P1Extractor(llm, repo, model_label="fake/model").run(
        filing_meta=META, sections=SECTIONS,
    )

    assert [row.finding_id for row in output.findings] == ["f1"]
    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert [row["tool"] for row in trace["tool_calls"]] == ["search_sections"]
    assert trace["outcome"] == "published"


def test_final_compiler_drops_only_bad_finding_after_shared_repair():
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(
        cik=META["cik"], ticker=META["ticker"], added_at="2025-02-01"
    ))
    repo.upsert_filing(Filing(
        accession_number=META["accession_number"], cik=META["cik"],
        form_type=META["form_type"], filed_at=META["filed_at"], raw_sha256="a" * 64,
    ))
    repo.track_company(META["cik"], at="2025-02-01")
    draft = _draft(
        _finding("f1", "Revenue increased"),
        _finding("f2", "This sentence is fabricated", headline="Unsupported change"),
    )
    llm = FakeLLMClient(responses=[_submit(draft), _submit(draft), _done()])

    output, _, _ = P1Extractor(llm, repo).run(filing_meta=META, sections=SECTIONS)

    assert [row.finding_id for row in output.findings] == ["f1"]
    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert trace["outcome"] == "partial"
    assert trace["dropped_findings"] == [
        {"finding_id": "f2", "error_codes": ["QUOTE_NOT_EXACT"]}
    ]
    certificate = PresentationService(repo).certificate(META["accession_number"])
    assert certificate is not None
    assert certificate.outcome == "partial"
    assert certificate.published_finding_ids == ["f1"]
    assert certificate.dropped_findings[0].finding_id == "f2"
    assert certificate.certificate_sha256 == PresentationService(repo).certificate(
        META["accession_number"]
    ).certificate_sha256
    assert PresentationService(repo, user_id="other").certificate(
        META["accession_number"]
    ) is None


def test_structured_sign_flip_is_compiler_error_before_skeptic():
    repo = Repo(init_db(":memory:"))
    metrics = MetricsBundle(results={
        "revenue_growth": MetricResult(
            metric="revenue_growth", status=MetricStatus.COMPUTED,
            value=0.1, formula_version="test", as_of="2025-02-01",
            direction_delta=10.0, direction_slack=2.0,
            direction_basis="current_minus_prior",
        )
    })
    wrong = _draft(_finding(
        "f1", "Revenue increased", metric_id="revenue_growth", direction="down"
    ))
    llm = FakeLLMClient(responses=[_submit(wrong), _submit(wrong)])

    output, _, _ = P1Extractor(llm, repo).run(
        filing_meta=META, sections=SECTIONS, metrics=metrics,
    )

    assert output.findings == []
    assert len(llm.calls) == 2  # no Skeptic call was needed to catch the sign flip
    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert trace["dropped_findings"][0]["error_codes"] == ["METRIC_CONTRADICTION"]
    assert trace["outcome"] == "metrics_only"
    assert output.classification.overall_severity == "routine"


def test_dropping_required_critical_finding_withholds_run():
    repo = Repo(init_db(":memory:"))
    meta = {**META, "form_type": "8-K"}
    sections = {"item_4_02": {"text": "Statements should no longer be relied upon."}}
    finding = _finding(
        "f1", "fabricated quote", severity="critical",
        critical_flag="item_4_02_non_reliance",
    )
    finding["evidence"][0].update({"form_type": "8-K", "section_key": "item_4_02"})
    draft = _draft(finding, form_type="8-K")
    llm = FakeLLMClient(responses=[_submit(draft), _submit(draft)])

    with pytest.raises(StageError, match="critical_coverage"):
        P1Extractor(llm, repo).run(filing_meta=meta, sections=sections)

    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert trace["outcome"] == "withheld"


@pytest.mark.parametrize(
    ("decimals", "expected"),
    [("-6", 500_000.0), ("0", 0.5), ("2", 0.005), ("INF", 0.0), (None, None), ("bad", None)],
)
def test_xbrl_rounding_slack(decimals, expected):
    assert xbrl_rounding_slack(decimals) == expected


def test_metric_propagates_decimals_and_conservative_direction_slack():
    facts = FactStore([
        Fact(
            taxonomy="us-gaap", tag="Revenues", unit="USD", value=120,
            decimals="-1", start="2024-01-01", end="2024-12-31",
        ),
        Fact(
            taxonomy="us-gaap", tag="Revenues", unit="USD", value=100,
            decimals="0", start="2023-01-01", end="2023-12-31",
        ),
    ])

    result = revenue_growth(facts, sector_from_sic("7372"), "2025-02-01")

    assert [row.decimals for row in result.inputs_used[:2]] == ["-1", "0"]
    assert result.direction_delta == 20
    assert result.direction_slack == 5.5
    assert result.deterministic_direction == "up"


@pytest.mark.parametrize(
    ("delta", "slack", "expected"),
    [(2.1, 2.0, "up"), (-2.1, 2.0, "down"), (2.0, 2.0, "flat"), (-2.0, 2.0, "flat")],
)
def test_direction_boundary_is_strict(delta, slack, expected):
    metric = MetricResult(
        metric="revenue_growth", status=MetricStatus.COMPUTED, value=0.0,
        formula_version="test", as_of="2025-02-01",
        direction_delta=delta, direction_slack=slack,
    )
    assert metric.deterministic_direction == expected


def test_snapshot_or_unknown_slack_direction_is_unavailable():
    for metric_id, metric in (
        ("liquidity_basics", MetricResult(
            metric="liquidity_basics", status=MetricStatus.COMPUTED, value=1.0,
            formula_version="test", as_of="2025-02-01",
        )),
        ("revenue_growth", MetricResult(
            metric="revenue_growth", status=MetricStatus.COMPUTED, value=0.1,
            formula_version="test", as_of="2025-02-01", direction_delta=10.0,
        )),
    ):
        repo = Repo(init_db(":memory:"))
        draft = _draft(_finding(
            "f1", "Revenue increased", metric_id=metric_id, direction="up"
        ))
        llm = FakeLLMClient(responses=[_submit(draft), _submit(draft)])
        P1Extractor(llm, repo).run(
            filing_meta=META, sections=SECTIONS,
            metrics=MetricsBundle(results={metric_id: metric}),
        )
        trace = json.loads(
            repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json
        )
        assert trace["dropped_findings"][0]["error_codes"] == [
            "METRIC_DIRECTION_UNAVAILABLE"
        ]


def test_budget_exhaustion_publishes_metrics_only_after_run_obligations_pass():
    repo = Repo(init_db(":memory:"))
    action = json.dumps({
        "action": "tool", "tool": "get_accounting_checks", "arguments": {},
    })
    llm = FakeLLMClient(responses=[action] * 7)

    output, _, _ = P1Extractor(llm, repo).run(filing_meta=META, sections=SECTIONS)

    assert output.findings == []
    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert trace["outcome"] == "metrics_only"
    assert trace["terminal_reason"] == "budget_exhausted"
    assert len(trace["tool_calls"]) == 6


def test_duplicate_tool_calls_are_recorded_and_reuse_same_bounded_result():
    repo = Repo(init_db(":memory:"))
    tool = json.dumps({
        "action": "tool", "tool": "search_sections",
        "arguments": {"queries": ["Revenue"]},
    })
    llm = FakeLLMClient(responses=[tool, tool, _submit(_draft()),])

    P1Extractor(llm, repo).run(filing_meta=META, sections=SECTIONS)

    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert len(trace["tool_calls"]) == 2
    assert trace["tool_calls"][0]["result_sha256"] == trace["tool_calls"][1][
        "result_sha256"
    ]


def test_preflight_trace_records_draft_hash_not_raw_model_output():
    repo = Repo(init_db(":memory:"))
    draft = _draft(_finding("f1", "Revenue increased", headline="Private draft phrase"))
    preflight = json.dumps({
        "action": "tool", "tool": "check_draft", "arguments": {"draft": draft},
    })
    llm = FakeLLMClient(responses=[preflight, _submit(draft), _done()])

    P1Extractor(llm, repo).run(filing_meta=META, sections=SECTIONS)

    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    arguments = trace["tool_calls"][0]["arguments"]
    assert arguments["finding_ids"] == ["f1"]
    assert len(arguments["draft_sha256"]) == 64
    assert "Private draft phrase" not in json.dumps(trace["tool_calls"])


def test_skeptic_objection_drops_only_targeted_finding_after_final_review():
    repo = Repo(init_db(":memory:"))
    draft = _draft(
        _finding("f1", "Revenue increased"),
        _finding("f2", "costs remained stable", headline="Costs remained stable"),
    )
    objection = _done([{"finding_id": "f2", "code": "LOW_CONFIDENCE"}])
    llm = FakeLLMClient(responses=[
        _submit(draft), objection, _submit(draft), objection,
    ])

    output, _, _ = P1Extractor(llm, repo).run(filing_meta=META, sections=SECTIONS)

    assert [row.finding_id for row in output.findings] == ["f1"]
    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert trace["dropped_findings"] == [
        {"finding_id": "f2", "error_codes": ["LOW_CONFIDENCE"]}
    ]
    assert trace["terminal_reason"] == "skeptic_blocked"


def test_provider_failure_withholds_and_records_terminal_reason():
    repo = Repo(init_db(":memory:"))

    def fail(_system, _user):
        raise RuntimeError("secret provider detail")

    with pytest.raises(StageError, match="provider_failed"):
        P1Extractor(FakeLLMClient(responder=fail), repo).run(
            filing_meta=META, sections=SECTIONS
        )

    trace = json.loads(repo.latest_analysis(META["accession_number"], "P1_TRACE").output_json)
    assert trace["outcome"] == "withheld"
    assert trace["terminal_reason"] == "provider_failed"
    assert "secret provider detail" not in json.dumps(trace)
