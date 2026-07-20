"""Blocking or incomplete verification must quarantine every LLM-derived byte."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.db import Analysis, Company, Filing, Repo, VerificationResult, init_db
from finwatch.demo import build_demo_db
from finwatch.digest import render_digest
from finwatch.presentation import PresentationService
from finwatch.web.app import create_app

ACCESSION = "0000000001-24-000001"
SECRETS = (
    "UNVERIFIED_HEADLINE_9137",
    "UNVERIFIED_P2_6248",
    "UNVERIFIED_CHECK_DETAIL_7359",
    "UNVERIFIED_STAGE_ERROR_8460",
    "UNVERIFIED_STAGE_DIAGNOSTIC_9571",
)


def _seed(path: str, *, blocking: bool) -> None:
    conn = init_db(path)
    repo = Repo(conn)
    repo.upsert_company(Company(cik="1", ticker="ZZZ", name="Z", added_at="t"))
    repo.upsert_company(Company(cik="1", ticker="ZZZ", added_at="t"))
    repo.track_company("1", at="t")
    repo.upsert_filing(Filing(
        accession_number=ACCESSION,
        cik="1",
        form_type="8-K",
        filed_at="2024-01-01",
        status="analyzed" if blocking else "verified",
    ))
    analysis_id = repo.insert_analysis(Analysis(
        accession_number=ACCESSION,
        ticker="ZZZ",
        stage="P1",
        model="openai/test",
        prompt_version="v",
        output_json=json.dumps({"headline": SECRETS[0]}),
        created_at="t",
    ))
    repo.insert_analysis(Analysis(
        accession_number=ACCESSION,
        ticker="ZZZ",
        stage="P2",
        model="openai/test",
        prompt_version="v",
        output_json=json.dumps({"net_read": SECRETS[1]}),
        created_at="t",
    ))
    if blocking:
        repo.insert_verification_results([
            VerificationResult(
                analysis_id=analysis_id,
                check_id="V1",
                verdict="fail",
                severity="blocking",
                detail=SECRETS[2],
                created_at="t",
            )
        ] + [
            VerificationResult(
                analysis_id=analysis_id,
                check_id=check_id,
                verdict="pass",
                severity="blocking",
                created_at="t",
            )
            for check_id in ("V4", "V5")
        ])
    repo.set_filing_stage(
        ACCESSION,
        "extract",
        "failed",
        at="t",
        error=SECRETS[3],
        diagnostics={"raw": SECRETS[4]},
    )
    conn.close()


@pytest.mark.parametrize("blocking", [True, False])
def test_all_presenters_withhold_blocking_or_incompletely_verified_llm_output(
    tmp_path, blocking
):
    db_path = tmp_path / "finwatch.db"
    _seed(str(db_path), blocking=blocking)
    conn = init_db(str(db_path))
    repo = Repo(conn)
    brief = PresentationService(repo).brief(since="2024-01-01").model_dump_json()
    detail = PresentationService(repo).filing(ACCESSION).model_dump_json()
    markdown = render_digest(repo, since="2024-01-01").markdown
    conn.close()

    client = TestClient(create_app(db_path=str(db_path), web_dist=tmp_path / "missing"))
    api_brief = client.get("/api/brief?since=2024-01-01").text
    api_detail = client.get(f"/api/filings/{ACCESSION}").text

    rendered = "\n".join((brief, detail, markdown, api_brief, api_detail))
    assert "LLM-derived analysis withheld" in rendered
    for secret in SECRETS:
        assert secret not in rendered


def test_only_deterministic_v2_details_cross_the_presentation_boundary(tmp_path):
    db_path = tmp_path / "finwatch.db"
    conn = build_demo_db(str(db_path))
    try:
        repo = Repo(conn)
        analysis = repo.latest_analysis("0000950170-24-048288", "P1")
        assert analysis is not None and analysis.id is not None
        secret = "UNVERIFIED_AUTHORED_4471"
        long_v2 = "A=100.0 L+E=<script>110.0\x00" + ("x" * 300)
        conn.execute(
            "UPDATE verification_results SET detail = ? "
            "WHERE analysis_id = ? AND check_id = 'V1'",
            (f"orphan number '{secret}' at pos 12", analysis.id),
        )
        conn.execute(
            "UPDATE verification_results SET detail = ? "
            "WHERE analysis_id = ? AND check_id = 'V2a'",
            (long_v2, analysis.id),
        )
        conn.commit()
        detail = PresentationService(repo).filing("0000950170-24-048288")
        assert detail is not None and detail.verification is not None
        by_id = {row.check_id: row for row in detail.verification.checks}
        assert by_id["V1"].detail is None
        assert secret not in detail.model_dump_json()
        sanitized = by_id["V2a"].detail
        assert sanitized is not None
        assert "A=100.0" in sanitized and "L+E=" in sanitized
        assert not any(char in sanitized for char in "<>\x00")
        assert len(sanitized) <= 200
    finally:
        conn.close()

    response = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing")
    ).get("/api/filings/0000950170-24-048288")
    assert response.status_code == 200
    assert secret not in response.text
