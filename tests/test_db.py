"""Data layer: schema install / legacy rejection + repository semantics."""
from __future__ import annotations

import os
import sqlite3
import stat

import pytest

from finwatch.db import (
    Company,
    Computation,
    Filing,
    FilingSection,
    SchemaVersionError,
    VerificationResult,
    XbrlFact,
    connect,
    init_db,
)
from finwatch.db.database import APPLICATION_ID, SCHEMA_VERSION


def test_schema_installs_with_application_id_and_version():
    conn = init_db(":memory:")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "companies", "filings", "filing_sections", "filing_stage_runs", "xbrl_facts",
        "analyses", "computations", "verification_results", "digests", "settings",
    } <= tables
    # dormant tables from the v0.2 schema are gone
    assert not ({"holdings", "prices", "analysis_claims", "signal_shadow_log"} & tables)


def test_reopening_a_current_database_is_a_no_op(tmp_path):
    path = tmp_path / "finwatch.db"
    init_db(path).close()
    conn = init_db(path)  # must not re-install or raise
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_legacy_or_foreign_database_is_rejected(tmp_path):
    path = tmp_path / "legacy.db"
    conn = connect(path)
    conn.executescript("CREATE TABLE holdings (cik TEXT);")
    conn.execute("PRAGMA user_version = 3")   # an old finwatch schema version
    conn.commit()
    conn.close()
    with pytest.raises(SchemaVersionError, match="different finwatch schema"):
        init_db(path)


def test_connections_use_wal_and_bounded_busy_wait(tmp_path):
    conn = connect(tmp_path / "finwatch.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


def test_fts5_virtual_table_created():
    conn = init_db(":memory:")
    row = conn.execute("SELECT name FROM sqlite_master WHERE name = 'section_fts'").fetchone()
    assert row is not None


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_init_db_secures_new_data_directory_and_database_file(tmp_path):
    db_path = tmp_path / "nested" / "data" / "finwatch.db"
    conn = init_db(db_path)
    conn.close()
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_connect_hardens_existing_db_without_chmodding_existing_parent(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    db_path = parent / "finwatch.db"
    db_path.touch(mode=0o666)
    db_path.chmod(0o666)
    conn = connect(db_path)
    conn.close()
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


# ---- companies (identity + tracking) ---------------------------------------
def test_company_upsert_preserves_identity_and_conditional_is_financial(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", name="Alpha", added_at="t"))
    # ingest supplies SIC -> classification updates, name preserved
    repo.upsert_company(
        Company(cik="1", ticker="AAA", sic_code="6021", is_financial=1, added_at="t2"))
    c = repo.get_company("1")
    assert c.name == "Alpha" and c.sic_code == "6021" and c.is_financial == 1
    # a later add with no SIC must NOT reset the classification
    repo.upsert_company(Company(cik="1", ticker="AAA", name="Alpha", added_at="t3"))
    assert repo.get_company("1").is_financial == 1


def test_company_lookup_by_ticker_case_insensitive(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    assert repo.get_company_by_ticker("aaa").cik == "1"


def test_track_and_untrack_company_preserves_the_issuer_row(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_company(Company(cik="2", ticker="BBB", added_at="t"))
    assert repo.list_tracked_ciks() == []
    repo.track_company("1", at="t1")
    repo.track_company("2", at="t1")
    assert [c.ticker for c in repo.list_tracked_companies()] == ["AAA", "BBB"]
    assert repo.list_tracked_ciks() == ["1", "2"]
    # untracking clears the marker but keeps the company row (audit/history retained)
    assert repo.untrack_company("1") is True
    assert repo.list_tracked_ciks() == ["2"]
    assert repo.get_company("1") is not None
    assert repo.untrack_company("1") is False  # already untracked


def test_track_is_idempotent_and_keeps_the_original_timestamp(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.track_company("1", at="first")
    repo.track_company("1", at="second")
    assert repo.get_company("1").tracked_at == "first"


def test_upsert_company_never_changes_tracking_state(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.track_company("1", at="t1")
    # a profile refresh (new name/sic, no tracked_at) must not untrack the company
    repo.upsert_company(
        Company(cik="1", ticker="AAA", name="Alpha", sic_code="6021", added_at="t2"))
    assert repo.get_company("1").tracked_at == "t1"


def test_filing_index_idempotent(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    f = Filing(accession_number="a-1", cik="1", form_type="10-K", filed_at="2024-01-01")
    assert repo.upsert_filing(f) is True
    assert repo.upsert_filing(f) is False  # second insert ignored
    assert repo.known_accessions("1") == {"a-1"}


def test_filing_stage_progress_persists_attempts_diagnostics_and_reset(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_filing(
        Filing(accession_number="a-1", cik="1", form_type="10-K", filed_at="2024-01-01")
    )
    repo.set_filing_stage("a-1", "parse", "running", at="t1")
    repo.set_filing_stage(
        "a-1", "parse", "failed", at="t2", error="no sections", diagnostics={"bytes": 12}
    )
    stage = repo.get_filing_stage("a-1", "parse")
    assert stage.status == "failed" and stage.attempts == 1
    assert stage.error == "no sections" and '"bytes": 12' in stage.diagnostics_json

    repo.reset_filing_stages("a-1", ["parse"])
    reset = repo.get_filing_stage("a-1", "parse")
    assert reset.status == "pending" and reset.attempts == 1 and reset.error is None


def test_replace_xbrl_facts_replaces_not_appends(repo):
    facts = [XbrlFact(cik="1", taxonomy="us-gaap", tag="Assets", value=100.0, instant="2024-01-01")]
    assert repo.replace_xbrl_facts("1", facts) == 1
    assert repo.replace_xbrl_facts("1", facts + facts) == 2
    assert repo.count_xbrl_facts("1") == 2  # prior single row was deleted first


def test_replace_xbrl_facts_rolls_back_on_insert_failure(repo):
    old = XbrlFact(cik="1", taxonomy="us-gaap", tag="Assets", value=100.0, instant="2024-01-01")
    repo.replace_xbrl_facts("1", [old])
    valid_new = old.model_copy(update={"tag": "Liabilities", "value": 50.0})
    invalid_new = XbrlFact.model_construct(
        cik="1", taxonomy="us-gaap", tag=None, value=25.0, dimensions_json="{}",
    )
    with pytest.raises(sqlite3.IntegrityError, match="xbrl_facts.tag"):
        repo.replace_xbrl_facts("1", [valid_new, invalid_new])
    stored = repo.list_xbrl_facts("1")
    assert len(stored) == 1 and stored[0].tag == "Assets" and stored[0].value == 100.0


def test_replace_filing_sections_rolls_back_rows_and_fts_together(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_filing(
        Filing(accession_number="a-1", cik="1", form_type="10-K", filed_at="2024-01-01")
    )
    old = FilingSection(
        accession_number="a-1", section_key="old", char_start=0, char_end=19,
        text="legacy evidence text", text_sha256="old-hash",
    )
    repo.replace_filing_sections("a-1", [old])
    valid_new = FilingSection(
        accession_number="a-1", section_key="new", char_start=0, char_end=19,
        text="fresh evidence text", text_sha256="new-hash",
    )
    invalid_new = FilingSection.model_construct(
        accession_number="a-1", section_key="invalid", char_start=20, char_end=26,
        text="broken", text_sha256=None, is_furnished=0,
    )
    with pytest.raises(sqlite3.IntegrityError, match="filing_sections.text_sha256"):
        repo.replace_filing_sections("a-1", [valid_new, invalid_new])
    stored = repo.list_filing_sections("a-1")
    assert [(s.section_key, s.text) for s in stored] == [("old", "legacy evidence text")]
    legacy_hits = repo.conn.execute(
        "SELECT COUNT(*) FROM section_fts WHERE section_fts MATCH 'legacy'"
    ).fetchone()[0]
    fresh_hits = repo.conn.execute(
        "SELECT COUNT(*) FROM section_fts WHERE section_fts MATCH 'fresh'"
    ).fetchone()[0]
    assert legacy_hits == 1 and fresh_hits == 0


def test_computation_recency_uses_as_of_then_id_not_insertion_order(repo):
    def computation(tool: str, as_of: str, marker: str) -> Computation:
        return Computation(
            ticker="AAA", tool=tool, args_json="{}", result_json=marker,
            status="computed", formula_version="test", as_of=as_of, created_at="t",
        )

    repo.insert_computations([
        computation("revenue_growth", "2025-03-31", "newest-date"),
        computation("revenue_growth", "2024-12-31", "older-inserted-later"),
        computation("cfo_trend", "2024-12-31", "same-date-first"),
        computation("cfo_trend", "2024-12-31", "same-date-second"),
        computation("revenue_growth", "2023-12-31", "oldest-inserted-last"),
    ])
    latest = {row.tool: row.result_json for row in repo.latest_computations("AAA")}
    assert latest == {"cfo_trend": "same-date-second", "revenue_growth": "newest-date"}
    historical = {
        row.tool: row.result_json for row in repo.computations_as_of("AAA", "2024-12-31")
    }
    assert historical == {
        "cfo_trend": "same-date-second", "revenue_growth": "older-inserted-later",
    }


def test_computation_batch_rolls_back_on_insert_failure(repo):
    valid = Computation(
        ticker="AAA", tool="revenue_growth", args_json="{}", result_json="{}",
        status="computed", formula_version="test", as_of="2024-12-31", created_at="t",
    )
    invalid = Computation.model_construct(
        ticker="AAA", tool=None, args_json="{}", result_json="{}",
        status="computed", formula_version="test", as_of="2024-12-31", created_at="t",
    )
    with pytest.raises(sqlite3.IntegrityError, match="computations.tool"):
        repo.insert_computations([valid, invalid])
    assert repo.count_computations("AAA") == 0


def test_verification_report_replacement_rolls_back_on_insert_failure(repo):
    old = VerificationResult(
        analysis_id=7, check_id="V1", verdict="pass", severity="blocking",
        detail="old report", created_at="t1",
    )
    repo.insert_verification_results([old])
    valid_new = old.model_copy(update={"check_id": "V4", "detail": "new report"})
    invalid_new = VerificationResult.model_construct(
        analysis_id=7, check_id=None, verdict="pass", severity="blocking",
        detail="invalid", created_at="t2",
    )
    with pytest.raises(sqlite3.IntegrityError, match="verification_results.check_id"):
        repo.replace_verification_results(7, [valid_new, invalid_new])
    stored = repo.list_verification_results(7)
    assert len(stored) == 1 and stored[0].check_id == "V1" and stored[0].detail == "old report"


def test_settings_roundtrip(repo):
    assert repo.get_setting("period") is None
    repo.set_setting("period", "90d")
    assert repo.get_setting("period") == "90d"
    repo.set_setting("period", "1y")
    assert repo.get_setting("period") == "1y"
