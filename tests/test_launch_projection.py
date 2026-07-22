import json

import pytest

from finwatch.db import Computation, Filing, Repo
from finwatch.db.repositories import VerificationResult
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.llm.schemas import P1Output
from finwatch.metrics.catalog import (
    STARTER_METRIC_EXPRESSIONS,
    STARTER_METRICS,
    MetricId,
)
from finwatch.metrics.envelope import MetricResult
from finwatch.presentation.canonical import build_filing_entry
from finwatch.presentation.projection import (
    GATE_WITHHELD_REASON,
    PIPELINE_FAILED_REASON,
    load_filing_projection,
)
from finwatch.presentation.service import PresentationService

_DPLS = "0001683168-24-004848"


def _entry(repo: Repo):
    filing = repo.get_filing(_DPLS)
    assert filing is not None
    return build_filing_entry(repo, load_filing_projection(repo, filing))


def test_pipeline_failure_is_not_reported_as_a_gate_refusal():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        accession = "0000320193-26-000099"
        repo.upsert_filing(
            Filing(
                accession_number=accession,
                cik="0000320193",
                form_type="8-K",
                filed_at="2026-05-01",
                status="failed",
            )
        )
        filing = repo.get_filing(accession)
        assert filing is not None
        entry = build_filing_entry(repo, load_filing_projection(repo, filing))
        assert entry.withheld is True
        assert entry.withheld_kind == "pipeline_failed"
        assert entry.outcome == "pipeline_failed"
        assert entry.withheld_reason == PIPELINE_FAILED_REASON
        assert entry.withheld_reason != GATE_WITHHELD_REASON
    finally:
        conn.close()


def test_launch_projection_requires_direct_evidence_for_every_finding():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        analysis = repo.latest_analysis(_DPLS, "P1")
        assert analysis is not None
        output = json.loads(analysis.output_json)
        output["findings"][0]["evidence"] = []
        conn.execute(
            "UPDATE analyses SET output_json = ? WHERE id = ?",
            (json.dumps(output), analysis.id),
        )
        conn.commit()

        entry = _entry(repo)

        assert entry.withheld is True
        assert entry.findings == []
        assert "Going concern doubt" not in entry.model_dump_json()
    finally:
        conn.close()


def test_raw_p1_contract_rejects_more_than_three_findings():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        analysis = repo.latest_analysis(_DPLS, "P1")
        assert analysis is not None
        output = json.loads(analysis.output_json)
        source = json.loads(json.dumps(output["findings"][0]))
        source["headline"] = "Additional concern"
        source["critical_flag"] = None
        output["findings"].extend([source, json.loads(json.dumps(source))])
        conn.execute(
            "UPDATE analyses SET output_json = ? WHERE id = ?",
            (json.dumps(output), analysis.id),
        )
        conn.commit()

        entry = _entry(repo)

        assert entry.withheld is True
        assert entry.findings == []
    finally:
        conn.close()


def test_one_bad_evidence_span_drops_only_its_finding():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        analysis = repo.latest_analysis(_DPLS, "P1")
        assert analysis is not None
        output = json.loads(analysis.output_json)
        # The first finding remains valid; corrupt only the second finding's span.
        evidence = output["findings"][1]["evidence"][0]
        evidence["char_start"] += 1
        filing = repo.get_filing(_DPLS)
        view = load_filing_projection(repo, filing)
        view.p1 = P1Output.model_validate(output)
        entry = build_filing_entry(repo, view)

        assert entry.withheld is False
        assert [finding.headline for finding in entry.findings] == ["Going concern doubt"]
    finally:
        conn.close()


def test_blocking_verifier_failure_exposes_no_llm_content():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        analysis = repo.latest_analysis(_DPLS, "P1")
        assert analysis is not None and analysis.id is not None
        repo.insert_verification_results(
            [
                VerificationResult(
                    analysis_id=analysis.id,
                    check_id="launch-mutation",
                    verdict="fail",
                    severity="blocking",
                    detail="secret model output",
                    created_at="2026-07-09T00:00:00Z",
                )
            ]
        )

        view = PresentationService(repo).brief(since=DEMO_SINCE)
        rendered = view.model_dump_json()

        assert "Going concern doubt" not in rendered
        assert "secret model output" not in rendered
        assert any(entry.ticker == "DPLS" for entry in view.withheld_filings)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("confidence", "gaps"),
    [
        ("low", []),
        ("high", ["Input was truncated before controls."]),
    ],
)
def test_model_reported_confidence_and_gaps_do_not_override_compiler_passes(
    confidence: str, gaps: list[str]
):
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        analysis = repo.latest_analysis(_DPLS, "P1")
        assert analysis is not None
        output = json.loads(analysis.output_json)
        output["extraction_confidence"] = confidence
        output["gaps"] = gaps
        filing = repo.get_filing(_DPLS)
        view = load_filing_projection(repo, filing)
        view.p1 = P1Output.model_validate(output)
        entry = build_filing_entry(repo, view)

        assert entry.withheld is False
        assert len(entry.findings) == 2
    finally:
        conn.close()


def test_section_hash_drift_withholds_exact_browser_projection():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        conn.execute(
            "UPDATE filing_sections SET text_sha256 = ? "
            "WHERE accession_number = ? AND section_key = 'auditor_report'",
            ("0" * 64, _DPLS),
        )
        conn.commit()

        entry = _entry(repo)

        assert entry.withheld is True
        assert entry.findings == []
    finally:
        conn.close()


def test_structured_metric_rows_carry_exact_persisted_source_identity():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        stored = repo.latest_computations("MSFT")
        view = PresentationService(repo).metrics("MSFT", as_of="2024-08-05")
    finally:
        conn.close()

    assert view is not None and view.rows
    source_ids = {row.id for row in stored}
    assert all(row.source_computation_id in source_ids for row in view.rows)
    assert all(row.effective_as_of <= "2024-08-05" for row in view.rows)


def test_computed_metric_projects_only_its_persisted_validated_inputs():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        stored = next(
            row for row in repo.latest_computations("MSFT")
            if row.tool == "revenue_growth"
        )
        persisted = json.loads(stored.result_json)
        view = PresentationService(repo).metrics("MSFT", as_of="2024-08-05")
    finally:
        conn.close()

    assert view is not None
    row = next(item for item in view.rows if item.source_computation_id == stored.id)
    assert row.derivation is not None
    assert row.derivation.formula_version == persisted["formula_version"]
    assert row.derivation.expression == STARTER_METRIC_EXPRESSIONS["revenue_growth"]
    assert len(row.derivation.inputs) == len(persisted["inputs_used"])
    for projected, source in zip(
        row.derivation.inputs, persisted["inputs_used"], strict=True
    ):
        period = (
            f"as of {source['instant']}"
            if source["instant"]
            else f"{source['period_start']} to {source['period_end']}"
            if source["period_start"] and source["period_end"]
            else f"period ended {source['period_end']}"
            if source["period_end"]
            else "period not stated"
        )
        assert projected.concept == source["tag"]
        assert projected.taxonomy == source["taxonomy"]
        assert projected.unit == source["unit_ref"]
        assert projected.period == period
        assert projected.accession == source["accession_number"]
    first = row.derivation.inputs[0]
    persisted_first = persisted["inputs_used"][0]
    assert first.model_dump() == {
        "concept": persisted_first["tag"],
        "taxonomy": persisted_first["taxonomy"],
        "value": "$168.1B",
        "unit": persisted_first["unit_ref"],
        "period": "2020-07-01 to 2021-06-30",
        "accession": persisted_first["accession_number"],
    }


def test_provenance_invalid_metric_withholds_its_derivation():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        stored = next(
            row for row in repo.latest_computations("MSFT")
            if row.tool == "revenue_growth"
        )
        corrupted = json.loads(stored.result_json)
        corrupted["metric"] = "cfo_trend"
        conn.execute(
            "UPDATE computations SET result_json = ? WHERE id = ?",
            (json.dumps(corrupted), stored.id),
        )
        conn.commit()

        view = PresentationService(repo).metrics("MSFT", as_of="2024-08-05")
    finally:
        conn.close()

    assert view is not None
    row = next(item for item in view.rows if item.source_computation_id == stored.id)
    assert row.state == "withheld"
    assert row.derivation is None


def test_unavailable_metric_projects_formula_with_no_fabricated_inputs():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        metric = MetricResult(
            metric="revenue_growth",
            status="unavailable",
            unavailable_missing=["revenue (2 annual periods)"],
            formula_version="revenue_growth.v5",
            as_of="2024-08-05",
        )
        repo.insert_computations([
            Computation(
                ticker="MSFT",
                tool=metric.metric,
                args_json="{}",
                result_json=metric.model_dump_json(),
                status=metric.status.value,
                formula_version=metric.formula_version,
                as_of=metric.as_of,
                created_at="2026-07-22T00:00:00Z",
            )
        ])
        view = PresentationService(repo).metrics("MSFT", as_of="2024-08-05")
    finally:
        conn.close()

    assert view is not None
    row = next(item for item in view.rows if item.metric == "Revenue growth")
    assert row.state == "unavailable"
    assert row.derivation is not None
    assert row.derivation.expression == STARTER_METRIC_EXPRESSIONS["revenue_growth"]
    assert row.derivation.inputs == []


def test_every_starter_metric_has_a_human_readable_expression():
    assert set(STARTER_METRIC_EXPRESSIONS) == set(STARTER_METRICS)
    assert set(STARTER_METRIC_EXPRESSIONS) == {metric.value for metric in MetricId}
    assert all(expression.strip() for expression in STARTER_METRIC_EXPRESSIONS.values())
