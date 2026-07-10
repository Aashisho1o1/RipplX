"""Data layer: schema/migrations + repository semantics."""
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
    Holding,
    MigrationError,
    Price,
    VerificationResult,
    XbrlFact,
    apply_migrations,
    connect,
    init_db,
)
from finwatch.db.database import SCHEMA_VERSION, _migration_sql, _schema_sql


def test_schema_applies_and_versions():
    conn = init_db(":memory:")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "companies", "holdings", "filings", "filing_sections", "xbrl_facts", "prices",
        "analyses", "analysis_claims", "computations", "verification_results",
        "signal_shadow_log", "digests", "settings", "filing_stage_runs",
    } <= tables


def test_connections_use_wal_and_bounded_busy_wait(tmp_path):
    conn = connect(tmp_path / "finwatch.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


def test_fts5_virtual_table_created():
    conn = init_db(":memory:")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'section_fts'"
    ).fetchone()
    assert row is not None


def test_migration_is_idempotent():
    conn = init_db(":memory:")
    assert apply_migrations(conn) == SCHEMA_VERSION  # re-run is a no-op
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v1_database_upgrades_through_all_ordered_migrations():
    conn = connect(":memory:")
    conn.executescript(_schema_sql())
    conn.execute("PRAGMA user_version = 1")

    assert apply_migrations(conn) == SCHEMA_VERSION
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'filing_stage_runs'"
    ).fetchone()
    assert table is not None
    indexes = {row[1] for row in conn.execute("PRAGMA index_list('holdings')")}
    assert "ux_holdings_cik" in indexes


def test_unique_holding_migration_fails_closed_on_preexisting_duplicates():
    conn = connect(":memory:")
    conn.executescript(_schema_sql())
    conn.executescript(_migration_sql("migration_002_filing_stage_runs.sql"))
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO companies (cik, ticker, added_at) VALUES ('1', 'AAA', 't')"
    )
    conn.executemany(
        """INSERT INTO holdings (cik, ticker, owned, added_at)
           VALUES ('1', 'AAA', 1, ?)""",
        [("t1",), ("t2",)],
    )
    conn.commit()

    with pytest.raises(MigrationError, match=r"duplicate holdings exist.*1 \(2 rows\)"):
        apply_migrations(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 2
    assert "ux_holdings_cik" not in {
        row[1] for row in conn.execute("PRAGMA index_list('holdings')")
    }


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


def test_company_upsert_preserves_identity_and_conditional_is_financial(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", name="Alpha", added_at="t"))
    # ingest supplies SIC -> classification updates, name preserved
    repo.upsert_company(Company(
        cik="1", ticker="AAA", sic_code="6021", sector_class="financial",
        is_financial=1, added_at="t2"))
    c = repo.get_company("1")
    assert c.name == "Alpha" and c.sic_code == "6021" and c.is_financial == 1
    # a later add with no SIC must NOT reset the classification
    repo.upsert_company(Company(cik="1", ticker="AAA", name="Alpha", added_at="t3"))
    assert repo.get_company("1").is_financial == 1


def test_company_upsert_empty_sic_repoll_keeps_consistent_classification(repo):
    # a bank ingested with a real SIC
    repo.upsert_company(Company(
        cik="1", ticker="JPM", sic_code="6021", sector_class="financial",
        is_financial=1, added_at="t"))
    # a later re-poll returns an EMPTY sic -> service computes ('general', is_financial=0)
    # with sic_code=None. Neither half of the SIC-derived pair may be clobbered.
    repo.upsert_company(Company(
        cik="1", ticker="JPM", sic_code=None, sector_class="general",
        is_financial=0, added_at="t2"))
    c = repo.get_company("1")
    assert c.sic_code == "6021"
    assert c.sector_class == "financial"  # not clobbered to 'general'
    assert c.is_financial == 1


def test_company_lookup_by_ticker_case_insensitive(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    assert repo.get_company_by_ticker("aaa").cik == "1"


def test_holding_upsert_updates_in_place(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    id1 = repo.upsert_holding(Holding(cik="1", ticker="AAA", owned=1, shares=5, added_at="t"))
    id2 = repo.upsert_holding(Holding(cik="1", ticker="AAA", owned=1, shares=9, added_at="t"))
    assert id1 == id2
    assert repo.get_holding_by_cik("1").shares == 9
    assert len(repo.list_holdings()) == 1


def test_holding_cik_uniqueness_is_enforced_by_sqlite(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_holding(Holding(cik="1", ticker="AAA", owned=1, added_at="t"))

    with pytest.raises(sqlite3.IntegrityError, match="holdings.cik"):
        repo.conn.execute(
            """INSERT INTO holdings (cik, ticker, owned, added_at)
               VALUES ('1', 'AAA', 1, 'duplicate')"""
        )
    repo.conn.rollback()
    assert len(repo.list_holdings()) == 1


def test_list_holdings_owned_filter(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_company(Company(cik="2", ticker="BBB", added_at="t"))
    repo.upsert_holding(Holding(cik="1", ticker="AAA", owned=1, added_at="t"))
    repo.upsert_holding(Holding(cik="2", ticker="BBB", owned=0, added_at="t"))
    assert [h.ticker for h in repo.list_holdings(owned=True)] == ["AAA"]
    assert [h.ticker for h in repo.list_holdings(owned=False)] == ["BBB"]
    assert repo.list_tracked_ciks() == ["1", "2"]


def test_filing_index_idempotent(repo):
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
    old = XbrlFact(
        cik="1", taxonomy="us-gaap", tag="Assets", value=100.0, instant="2024-01-01"
    )
    repo.replace_xbrl_facts("1", [old])
    valid_new = old.model_copy(update={"tag": "Liabilities", "value": 50.0})
    invalid_new = XbrlFact.model_construct(
        cik="1",
        taxonomy="us-gaap",
        tag=None,
        value=25.0,
        dimensions_json="{}",
    )

    with pytest.raises(sqlite3.IntegrityError, match="xbrl_facts.tag"):
        repo.replace_xbrl_facts("1", [valid_new, invalid_new])

    stored = repo.list_xbrl_facts("1")
    assert len(stored) == 1
    assert stored[0].tag == "Assets"
    assert stored[0].value == 100.0


def test_replace_filing_sections_rolls_back_rows_and_fts_together(repo):
    repo.upsert_filing(
        Filing(accession_number="a-1", cik="1", form_type="10-K", filed_at="2024-01-01")
    )
    old = FilingSection(
        accession_number="a-1",
        section_key="old",
        char_start=0,
        char_end=19,
        text="legacy evidence text",
        text_sha256="old-hash",
    )
    repo.replace_filing_sections("a-1", [old])
    valid_new = FilingSection(
        accession_number="a-1",
        section_key="new",
        char_start=0,
        char_end=19,
        text="fresh evidence text",
        text_sha256="new-hash",
    )
    invalid_new = FilingSection.model_construct(
        accession_number="a-1",
        section_key="invalid",
        char_start=20,
        char_end=26,
        text="broken",
        text_sha256=None,
        is_furnished=0,
    )

    with pytest.raises(sqlite3.IntegrityError, match="filing_sections.text_sha256"):
        repo.replace_filing_sections("a-1", [valid_new, invalid_new])

    stored = repo.list_filing_sections("a-1")
    assert [(section.section_key, section.text) for section in stored] == [
        ("old", "legacy evidence text")
    ]
    legacy_hits = repo.conn.execute(
        "SELECT COUNT(*) FROM section_fts WHERE section_fts MATCH 'legacy'"
    ).fetchone()[0]
    fresh_hits = repo.conn.execute(
        "SELECT COUNT(*) FROM section_fts WHERE section_fts MATCH 'fresh'"
    ).fetchone()[0]
    assert legacy_hits == 1
    assert fresh_hits == 0


def test_computation_recency_uses_as_of_then_id_not_insertion_order(repo):
    def computation(tool: str, as_of: str, marker: str) -> Computation:
        return Computation(
            ticker="AAA",
            tool=tool,
            args_json="{}",
            result_json=marker,
            status="computed",
            formula_version="test",
            as_of=as_of,
            created_at="t",
        )

    repo.insert_computations(
        [
            computation("revenue_growth", "2025-03-31", "newest-date"),
            computation("revenue_growth", "2024-12-31", "older-inserted-later"),
            computation("cfo_trend", "2024-12-31", "same-date-first"),
            computation("cfo_trend", "2024-12-31", "same-date-second"),
            computation("revenue_growth", "2023-12-31", "oldest-inserted-last"),
        ]
    )

    latest = {row.tool: row.result_json for row in repo.latest_computations("AAA")}
    assert latest == {
        "cfo_trend": "same-date-second",
        "revenue_growth": "newest-date",
    }
    historical = {
        row.tool: row.result_json
        for row in repo.computations_as_of("AAA", "2024-12-31")
    }
    assert historical == {
        "cfo_trend": "same-date-second",
        "revenue_growth": "older-inserted-later",
    }


def test_computation_batch_rolls_back_on_insert_failure(repo):
    valid = Computation(
        ticker="AAA",
        tool="revenue_growth",
        args_json="{}",
        result_json="{}",
        status="computed",
        formula_version="test",
        as_of="2024-12-31",
        created_at="t",
    )
    invalid = Computation.model_construct(
        ticker="AAA",
        tool=None,
        args_json="{}",
        result_json="{}",
        status="computed",
        formula_version="test",
        as_of="2024-12-31",
        created_at="t",
    )

    with pytest.raises(sqlite3.IntegrityError, match="computations.tool"):
        repo.insert_computations([valid, invalid])

    assert repo.count_computations("AAA") == 0


def test_verification_report_replacement_rolls_back_on_insert_failure(repo):
    old = VerificationResult(
        analysis_id=7,
        check_id="V1",
        verdict="pass",
        severity="blocking",
        detail="old report",
        created_at="t1",
    )
    repo.insert_verification_results([old])
    valid_new = old.model_copy(update={"check_id": "V4", "detail": "new report"})
    invalid_new = VerificationResult.model_construct(
        analysis_id=7,
        check_id=None,
        verdict="pass",
        severity="blocking",
        detail="invalid",
        created_at="t2",
    )

    with pytest.raises(sqlite3.IntegrityError, match="verification_results.check_id"):
        repo.replace_verification_results(7, [valid_new, invalid_new])

    stored = repo.list_verification_results(7)
    assert len(stored) == 1
    assert stored[0].check_id == "V1"
    assert stored[0].detail == "old report"


def test_prices_close_on_or_before(repo):
    repo.upsert_prices([
        Price(ticker="AAA", date="2024-01-02", close=10.0),
        Price(ticker="AAA", date="2024-01-05", close=11.0),
    ])
    assert repo.close_on_or_before("AAA", "2024-01-06") == 11.0
    assert repo.close_on_or_before("aaa", "2024-01-03") == 10.0  # on-or-before + case-insensitive
    assert repo.close_on_or_before("AAA", "2024-01-01") is None
    repo.upsert_prices([Price(ticker="AAA", date="2024-01-05", close=12.0)])  # replace on conflict
    assert repo.close_on_or_before("AAA", "2024-01-05") == 12.0


def test_settings_roundtrip(repo):
    assert repo.get_setting("risk_tolerance") is None
    repo.set_setting("risk_tolerance", "moderate")
    assert repo.get_setting("risk_tolerance") == "moderate"
    repo.set_setting("risk_tolerance", "aggressive")
    assert repo.get_setting("risk_tolerance") == "aggressive"
