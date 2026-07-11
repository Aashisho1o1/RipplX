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
from finwatch.digest.render import render_brief_markdown
from finwatch.presentation.models import (
    BriefPeriodView,
    BriefPortfolioView,
    BriefView,
    EvidenceView,
    FilingDigestEntry,
    FindingView,
    IssuerMetricsView,
    MetricRowView,
)
from finwatch.presentation.service import PresentationService


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
    for header in (
        "# finwatch digest",
            "## AI-selected changes (evidence verified)",
        "## Verified numbers",
        "## Open questions",
        "## Boring filings",
    ):
        assert header in md, header
    # verification came out clean — no filing routed to manual review
    assert "manual review required" not in md


def test_demo_findings_are_evidence_backed_with_edgar_links():
    md = _demo_markdown().markdown
    assert "DPLS — 10-K" in md and "TWKS — 8-K" in md
    assert md.count("_(AI: CRITICAL)_") >= 2
    assert "https://www.sec.gov/Archives/" in md          # EDGAR citation link
    assert "going concern" in md.lower()                  # verbatim evidence snippet


def test_demo_verified_numbers_table_is_formula_stamped_and_checked():
    md = _demo_markdown().markdown
    assert "### MSFT" in md
    assert "Revenue growth" in md and "revenue_growth.v3" in md
    assert "| ✓ |" in md                                  # verifier check mark
    # a holding with no XBRL facts degrades to one honest line, not six "unavailable" rows
    assert "DPLS:** no verified financials yet" in md


def test_demo_boring_and_disclaimer():
    md = _demo_markdown().markdown
    assert "2 routine filing(s) with no material findings" in md
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
def test_markdown_is_a_pure_serialization_of_the_canonical_brief():
    url = "https://www.sec.gov/Archives/example.htm"
    brief = BriefView(
        period=BriefPeriodView(
            covered="2024-01-01 → 2024-06-30",
            filings_in_window=2,
            analyzed_filings=2,
        ),
        portfolio=BriefPortfolioView(owned=["ZZZ"], watching=["YYY"]),
        answer="One evidence-backed change needs attention.",
        answer_posture="risk_review",
        filings=[
            FilingDigestEntry(
                accession="a-1",
                ticker="ZZZ",
                form="8-K",
                filed="2024-06-15",
                edgar_url=url,
                findings=[
                    FindingView(
                        finding_id="finding-1",
                        headline="Auditor resigned",
                        severity="HIGH",
                        evidence=[
                            EvidenceView(
                                claim_id="claim-1",
                                accession="a-1",
                                section_key="item_4_01",
                                char_start=0,
                                char_end=24,
                                quote=(
                                    "The auditor resigned.\nEffective immediately. "
                                    "<script>alert()</script>"
                                ),
                                section_sha256="a" * 64,
                                edgar_url=url,
                            )
                        ],
                    )
                ],
            )
        ],
        verified_numbers=[
            IssuerMetricsView(
                ticker="ZZZ",
                owned=True,
                rows=[
                    MetricRowView(
                        metric="Revenue growth",
                        value="+5.0% YoY | verified",
                        formula="revenue_growth.v1",
                        state="computed",
                        state_label="Computed from SEC XBRL facts",
                        source_computation_id=7,
                        effective_as_of="2024-06-15",
                    )
                ],
            )
        ],
        open_questions=["ZZZ: confirm successor auditor"],
        boring_filings="1 routine filing with no material findings (YYY 10-Q).",
    )

    markdown = render_brief_markdown(brief)

    assert markdown == render_brief_markdown(brief)
    for expected in (
        brief.answer,
        "Auditor resigned",
        "The auditor resigned. Effective immediately.",
        "Revenue growth",
        "2024-06-15",
        "ZZZ: confirm successor auditor",
        brief.boring_filings,
        brief.disclaimer,
    ):
        assert expected in markdown
    assert "+5.0% YoY \\| verified" in markdown
    assert "| Metric | Value | Computed as of | Formula | ✓ |" in markdown
    assert "claim-1" not in markdown  # internal identity is not a second content surface
    assert "<script>" not in markdown
    assert "&lt;script&gt;alert()&lt;/script&gt;" in markdown


def test_render_digest_serializes_the_same_brief_used_by_the_browser_api():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        canonical = PresentationService(repo).brief(since=DEMO_SINCE)
        rendered = render_digest(repo, since=DEMO_SINCE)
    finally:
        conn.close()

    assert rendered.markdown == render_brief_markdown(canonical)
    assert rendered.accessions == [
        "0000320193-26-000011",
        "0000320193-24-000081",
        "0001683168-24-004848",
        "0000950170-24-048288",
        "0001866550-24-000006",
    ]


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


def _seed_min(repo, *, thesis, severity="routine"):
    repo.upsert_company(Company(cik="1", ticker="ZZZ", name="Z", sic_code="7372",
                                is_financial=0, added_at="t"))
    repo.upsert_holding(Holding(cik="1", ticker="ZZZ", owned=1, shares=10, cost_basis=5.0,
                                thesis=thesis, added_at="t"))
    repo.upsert_filing(Filing(accession_number="a-1", cik="1", form_type="10-Q",
                              filed_at="2024-06-15"))
    p1 = {"accession_number": "a-1", "ticker": "ZZZ", "form_type": "10-Q",
          "classification": {"overall_severity": severity},
          "findings": [], "extraction_confidence": "high", "gaps": []}
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


def test_since_until_window_filters_filings():
    repo = Repo(init_db(":memory:"))
    _seed_min(repo, thesis="x")  # filed 2024-06-15
    assert "a-1" in render_digest(repo, since="2024-01-01").accessions
    assert "a-1" not in render_digest(repo, since="2024-07-01").accessions   # before window
    assert "a-1" not in render_digest(repo, until="2024-01-01").accessions   # after window


def test_routine_filing_lands_in_boring_not_dropped():
    # A routine filing with no evidence-backed finding must still appear in the compact
    # boring summary, never silently vanish from the canonical brief.
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik="9", ticker="QQQ", name="Q", sic_code="7372",
                                is_financial=0, added_at="t"))
    repo.upsert_holding(Holding(cik="9", ticker="QQQ", owned=0, added_at="t"))
    accession = "0000000009-24-000001"
    repo.upsert_filing(Filing(accession_number=accession, cik="9", form_type="8-K",
                              filed_at="2024-06-10"))
    p1 = {"accession_number": accession, "ticker": "QQQ", "form_type": "8-K",
          "classification": {"overall_severity": "routine"},
          "findings": [], "extraction_confidence": "high", "gaps": []}
    aid = repo.insert_analysis(Analysis(
        accession_number=accession, ticker="QQQ", stage="P1", model="m",
        prompt_version="v", output_json=json.dumps(p1), created_at="t"
    ))
    p2 = {"accession_number": accession, "records_affected": [{
        "ticker": "QQQ", "owned": False, "impact_class": "no_impact", "channels": {},
        "guidance_direction": "none_stated", "liquidity_read": "unclear",
        "net_direction": "neutral", "thesis_check": {"verdict": "not_assessable"},
        "net_read": {"text": "No portfolio impact."}, "confidence": "low"}],
        "claims": [], "portfolio_level_notes": None}
    repo.insert_analysis(Analysis(accession_number=accession, ticker="QQQ", stage="P2", model="m",
                                  prompt_version="v", output_json=json.dumps(p2), created_at="t"))
    _mark_verified(repo, aid, accession)
    md = render_digest(repo, since="2024-01-01").markdown
    assert "1 routine filing(s) with no material findings (QQQ 8-K)" in md


def test_empty_db_renders_gracefully():
    md = render_digest(Repo(init_db(":memory:"))).markdown
    assert "# finwatch digest" in md
    assert "_No evidence-backed changes were selected in this window._" in md


# ---- CLI -------------------------------------------------------------------
def test_cli_demo_needs_no_api_key_or_user_agent(monkeypatch):
    from typer.testing import CliRunner

    from finwatch.cli import app

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)   # zero-config: must still work
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "# finwatch digest" in result.output and "AI-selected changes" in result.output
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
    result = CliRunner().invoke(app, ["digest", "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists() and "## AI-selected changes" in out.read_text(encoding="utf-8")

    rows = Repo(init_db(str(db))).list_digests()
    assert len(rows) == 1 and json.loads(rows[0].filings_json)          # accessions recorded
    assert rows[0].markdown_path == str(out)
    assert rows[0].since is None
    assert rows[0].until is None


def test_cli_digest_rejects_historical_replay_options():
    from typer.testing import CliRunner

    from finwatch.cli import app

    for option in ("--since", "--until"):
        result = CliRunner().invoke(app, ["digest", option, "2024-01-01"])
        assert result.exit_code != 0
