"""Phase 7 — digest renderer + zero-key demo.

The demo drives the REAL launch pipeline (P0→P1→metrics→P2→verify) over bundled fixtures,
so one end-to-end test exercises every digest section; focused unit tests cover window
filtering, the no-thesis note, and metric degradation.
"""
from __future__ import annotations

import json
import time

from finwatch.db import (
    Analysis,
    Company,
    Filing,
    Holding,
    Repo,
    SignalShadowLog,
    VerificationResult,
    init_db,
)
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.digest import render_digest


# ---- end-to-end demo -------------------------------------------------------
def _demo_markdown():
    conn = build_demo_db()
    try:
        return render_digest(Repo(conn), since=DEMO_SINCE)
    finally:
        conn.close()


def test_demo_runs_fast_and_covers_every_section():
    t0 = time.time()
    result = _demo_markdown()
    elapsed = time.time() - t0
    assert elapsed < 30  # DoD budget is 60s incl. process start; the render itself is sub-second
    md = result.markdown
    assert len(result.accessions) == 5
    for header in ("# finwatch digest", "## Critical red flags", "## What changed",
                   "## Thesis impact", "## Verified numbers", "## Open questions",
                   "## Boring filings"):
        assert header in md, header
    # verification came out clean — no filing routed to manual review
    assert "manual review required" not in md


def test_demo_critical_flags_are_claim_backed_with_edgar_links():
    md = _demo_markdown().markdown
    assert "DPLS — 10-K" in md and "· CRITICAL" in md
    assert "TWKS — 8-K" in md and md.count("· CRITICAL") >= 2
    assert "**going_concern** (critical)" in md
    assert "https://www.sec.gov/Archives/" in md          # EDGAR citation link
    assert "going concern" in md.lower()                  # verbatim evidence snippet


def test_demo_verified_numbers_table_is_formula_stamped_and_checked():
    md = _demo_markdown().markdown
    assert "### MSFT" in md
    assert "Revenue growth" in md and "revenue_growth.v1" in md
    assert "| ✓ |" in md                                  # verifier check mark
    # a holding with no XBRL facts degrades to one honest line, not six "unavailable" rows
    assert "DPLS:** no verified financials yet" in md


def test_demo_thesis_and_boring_and_disclaimer():
    md = _demo_markdown().markdown
    assert "**DPLS:** thesis broken" in md
    assert "3 routine filing(s) with no material findings" in md
    assert "Not individualized investment advice." in md


def test_legacy_p3_and_shadow_rows_are_never_rendered():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        accession = "0001683168-24-004848"
        repo.insert_analysis(Analysis(
            accession_number=accession,
            ticker="DPLS",
            stage="P3",
            model="legacy/model",
            prompt_version="legacy",
            output_json=json.dumps({"rationale": "LEGACY_P3_SECRET"}),
            created_at="t",
        ))
        repo.insert_shadow_log(SignalShadowLog(
            accession_number=accession,
            ticker="DPLS",
            review_posture="critical_review",
            hypothetical_signal="STRONG_REVIEW_SELL",
            rules_fired_json="[]",
            rules_skipped_json="[]",
            computed_inputs_json="[]",
            created_at="t",
        ))
        md = render_digest(repo, since=DEMO_SINCE).markdown
    finally:
        conn.close()

    assert "Shadow signals" not in md
    assert "STRONG_REVIEW_SELL" not in md
    assert "LEGACY_P3_SECRET" not in md


# ---- focused renderer unit tests -------------------------------------------
def _mark_verified(repo: Repo, analysis_id: int, accession: str) -> None:
    repo.insert_verification_results([
        VerificationResult(
            analysis_id=analysis_id,
            check_id=check_id,
            verdict="pass",
            severity="blocking",
            created_at="t",
        )
        for check_id in ("V1", "V4", "V5")
    ])
    repo.set_filing_status(accession, "verified", processed_at="t")


def _seed_min(repo, *, thesis, severity="medium"):
    repo.upsert_company(Company(cik="1", ticker="ZZZ", name="Z", sic_code="7372",
                                is_financial=0, added_at="t"))
    repo.upsert_holding(Holding(cik="1", ticker="ZZZ", owned=1, shares=10, cost_basis=5.0,
                                thesis=thesis, added_at="t"))
    repo.upsert_filing(Filing(accession_number="a-1", cik="1", form_type="10-Q",
                              filed_at="2024-06-15"))
    p1 = {"accession_number": "a-1", "ticker": "ZZZ", "form_type": "10-Q",
          "classification": {"items_8k": [], "overall_severity": severity},
          "claims": [], "material_items": [], "guidance_direction": {"value": "none_stated"},
          "red_flags": [], "extraction_confidence": "high", "gaps": []}
    aid = repo.insert_analysis(Analysis(accession_number="a-1", ticker="ZZZ", stage="P1",
                                        model="m", prompt_version="v", output_json=json.dumps(p1),
                                        created_at="t"))
    p2 = {"accession_number": "a-1", "records_affected": [{
        "ticker": "ZZZ", "owned": True, "impact_class": "direct", "channels": {},
        "guidance_direction": "none_stated", "liquidity_read": "stable",
        "net_direction": "neutral", "thesis_check": {"verdict": "intact"},
        "net_read": {"text": "Nothing load-bearing changed."}, "confidence": "medium"}],
        "claims": [], "portfolio_level_notes": None}
    repo.insert_analysis(Analysis(accession_number="a-1", ticker="ZZZ", stage="P2",
                                  model="m", prompt_version="v", output_json=json.dumps(p2),
                                  created_at="t"))
    _mark_verified(repo, aid, "a-1")
    return aid


def test_no_thesis_note_rendered_when_thesis_missing():
    repo = Repo(init_db(":memory:"))
    _seed_min(repo, thesis=None)
    md = render_digest(repo, since="2024-01-01").markdown
    assert "No thesis provided." in md
    assert "cannot say whether this weakens your original reason" in md


def test_thesis_verdict_rendered_when_thesis_present():
    repo = Repo(init_db(":memory:"))
    _seed_min(repo, thesis="growth compounds")
    md = render_digest(repo, since="2024-01-01").markdown
    assert "**ZZZ:** thesis intact" in md
    assert "No thesis provided." not in md


def test_since_until_window_filters_filings():
    repo = Repo(init_db(":memory:"))
    _seed_min(repo, thesis="x")  # filed 2024-06-15
    assert "a-1" in render_digest(repo, since="2024-01-01").accessions
    assert "a-1" not in render_digest(repo, since="2024-07-01").accessions   # before window
    assert "a-1" not in render_digest(repo, until="2024-01-01").accessions   # after window


def test_material_no_impact_filing_lands_in_boring_not_dropped():
    # A medium filing whose P2 finds no portfolio impact must still appear (boring line),
    # never silently vanish from every section (determinism doctrine: no silent skips).
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="9", ticker="QQQ", name="Q", sic_code="7372",
                                is_financial=0, added_at="t"))
    repo.upsert_filing(Filing(accession_number="q-1", cik="9", form_type="8-K",
                              filed_at="2024-06-10"))
    p1 = {"accession_number": "q-1", "ticker": "QQQ", "form_type": "8-K",
          "classification": {"items_8k": [], "overall_severity": "medium"},
          "claims": [], "material_items": [], "guidance_direction": {"value": "none_stated"},
          "red_flags": [], "extraction_confidence": "high", "gaps": []}
    aid = repo.insert_analysis(Analysis(
        accession_number="q-1", ticker="QQQ", stage="P1", model="m",
        prompt_version="v", output_json=json.dumps(p1), created_at="t"
    ))
    p2 = {"accession_number": "q-1", "records_affected": [{
        "ticker": "QQQ", "owned": False, "impact_class": "no_impact", "channels": {},
        "guidance_direction": "none_stated", "liquidity_read": "unclear",
        "net_direction": "neutral", "thesis_check": {"verdict": "not_assessable"},
        "net_read": {"text": "No portfolio impact."}, "confidence": "low"}],
        "claims": [], "portfolio_level_notes": None}
    repo.insert_analysis(Analysis(accession_number="q-1", ticker="QQQ", stage="P2", model="m",
                                  prompt_version="v", output_json=json.dumps(p2), created_at="t"))
    _mark_verified(repo, aid, "q-1")
    md = render_digest(repo, since="2024-01-01").markdown
    assert "1 routine filing(s) with no material findings (QQQ 8-K)" in md


def test_empty_db_renders_gracefully():
    md = render_digest(Repo(init_db(":memory:"))).markdown
    assert "# finwatch digest" in md
    assert "_None. No critical or high-severity findings in this window._" in md


# ---- CLI -------------------------------------------------------------------
def test_cli_demo_needs_no_api_key_or_user_agent(monkeypatch):
    from typer.testing import CliRunner

    from finwatch.cli import app

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)   # zero-config: must still work
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "# finwatch digest" in result.output and "Critical red flags" in result.output
    assert "Shadow signals" not in result.output
    assert CliRunner().invoke(app, ["demo", "--signals"]).exit_code != 0


def test_cli_digest_writes_file_and_persists_digest_row(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from finwatch.cli import app

    db = tmp_path / "fw.db"
    build_demo_db(str(db)).close()          # seed a file-backed DB via the demo pipeline
    out = tmp_path / "digest.md"

    monkeypatch.setenv("SEC_USER_AGENT", "Test t@e.com")
    monkeypatch.setenv("FINWATCH_DB", str(db))
    result = CliRunner().invoke(
        app, ["digest", "--since", DEMO_SINCE, "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists() and "## Critical red flags" in out.read_text(encoding="utf-8")

    rows = Repo(init_db(str(db))).list_digests()
    assert len(rows) == 1 and json.loads(rows[0].filings_json)          # accessions recorded
    assert rows[0].markdown_path == str(out)
