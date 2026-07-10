import json

import pytest

from finwatch.db import Repo
from finwatch.db.repositories import VerificationResult
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.presentation.canonical import build_filing_entry
from finwatch.presentation.projection import load_filing_projection
from finwatch.presentation.service import PresentationService

_DPLS = "0001683168-24-004848"


def _entry(repo: Repo):
    filing = repo.get_filing(_DPLS)
    assert filing is not None
    return build_filing_entry(repo, load_filing_projection(repo, filing))


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

        assert entry.manual_review is True
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

        assert entry.manual_review is True
        assert entry.findings == []
    finally:
        conn.close()


def test_one_bad_evidence_span_withholds_every_finding():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        analysis = repo.latest_analysis(_DPLS, "P1")
        assert analysis is not None
        output = json.loads(analysis.output_json)
        # The first finding remains valid; corrupt only the second finding's span.
        evidence = output["findings"][1]["evidence"][0]
        evidence["char_start"] += 1
        conn.execute(
            "UPDATE analyses SET output_json = ? WHERE id = ?",
            (json.dumps(output), analysis.id),
        )
        conn.commit()

        entry = _entry(repo)

        assert entry.manual_review is True
        assert entry.findings == []
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
def test_incomplete_persisted_extraction_is_withheld_despite_old_passes(
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
        conn.execute(
            "UPDATE analyses SET output_json = ? WHERE id = ?",
            (json.dumps(output), analysis.id),
        )
        conn.commit()

        entry = _entry(repo)

        assert entry.manual_review is True
        assert entry.findings == []
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

        assert entry.manual_review is True
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
