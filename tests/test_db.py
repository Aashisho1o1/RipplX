"""Data layer: schema install / legacy rejection + repository semantics."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat

import pytest

from finwatch.db import (
    LOCAL_USER_ID,
    Analysis,
    Company,
    Computation,
    Filing,
    FilingSection,
    SchemaVersionError,
    User,
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
        "users", "companies", "user_companies", "user_preferences", "filings",
        "filing_sections", "filing_stage_runs", "xbrl_facts", "analyses",
        "computations", "verification_results", "digests", "settings",
    } <= tables
    local_email = conn.execute(
        "SELECT email FROM users WHERE id = ?", (LOCAL_USER_ID,)
    ).fetchone()[0]
    assert local_email == "local@finwatch.invalid"
    company_columns = {row[1] for row in conn.execute("PRAGMA table_info(companies)")}
    assert "tracked_at" not in company_columns
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


@pytest.mark.parametrize("old_version", [4, 5])
def test_old_schema_fails_with_clear_backup_and_reset_instruction(tmp_path, old_version):
    path = tmp_path / f"schema-v{old_version}.db"
    conn = connect(path)
    conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version = {old_version}")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaVersionError, match="Back up the directory and start fresh"):
        init_db(path)


def test_unmarked_nonempty_database_is_rejected_and_connection_is_released(tmp_path):
    path = tmp_path / "unmarked.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated_data (value TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaVersionError, match="non-empty database"):
        init_db(path)

    # The failed initializer must not retain a handle that prevents a clean backup/reset.
    path.rename(tmp_path / "unmarked.backup.db")


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


# ---- users + private workspace ---------------------------------------------
def test_user_create_lookup_and_last_login_update(repo):
    user = User(
        id="user-a",
        email="person@example.com",
        created_at="created",
        last_login_at="first-login",
    )
    assert repo.create_user(user) is True
    duplicate = user.model_copy(update={"id": "user-b", "email": "PERSON@example.com"})
    assert repo.create_user(duplicate) is False
    assert repo.get_user("user-a") == user
    assert repo.get_user_by_email("PERSON@example.com") == user
    assert repo.update_user_last_login("user-a", at="second-login") is True
    assert repo.get_user("user-a").last_login_at == "second-login"
    assert repo.update_user_last_login("missing", at="never") is False


def test_user_period_is_private_and_upserts(repo):
    for user_id in ("user-a", "user-b"):
        assert repo.create_user(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                created_at="created",
                last_login_at="login",
            )
        )
    assert repo.get_user_period("user-a") is None
    repo.set_user_period("user-a", "30d")
    repo.set_user_period("user-b", "1y")
    repo.set_user_period("user-a", "90d")
    assert repo.get_user_period("user-a") == "90d"
    assert repo.get_user_period("user-b") == "1y"


# ---- companies (shared identity + private tracking) ------------------------
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
    # untracking deletes private membership but keeps shared issuer/history rows
    assert repo.untrack_company("1") is True
    assert repo.list_tracked_ciks() == ["2"]
    assert repo.get_company("1") is not None
    assert repo.untrack_company("1") is False  # already untracked


def test_track_is_idempotent_and_keeps_the_original_timestamp(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.track_company("1", at="first")
    repo.track_company("1", at="second")
    assert repo.get_user_company(LOCAL_USER_ID, "1").tracked_at == "first"


def test_user_tracking_is_isolated_and_company_refresh_does_not_change_it(repo):
    for user_id in ("user-a", "user-b"):
        assert repo.create_user(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                created_at="created",
                last_login_at="login",
            )
        )
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_company(Company(cik="2", ticker="BBB", added_at="t"))
    repo.track_company("1", at="t1", user_id="user-a")
    repo.track_company("2", at="t2", user_id="user-b")
    # A shared profile refresh must not change either user's private tracking rows.
    repo.upsert_company(
        Company(cik="1", ticker="AAA", name="Alpha", sic_code="6021", added_at="t2"))
    assert repo.list_tracked_ciks("user-a") == ["1"]
    assert repo.list_tracked_ciks("user-b") == ["2"]
    assert [c.ticker for c in repo.list_tracked_companies("user-a")] == ["AAA"]
    assert repo.count_tracked_companies("user-a") == 1
    assert repo.count_tracked_companies("user-b") == 1
    assert repo.get_user_company("user-a", "1").tracked_at == "t1"
    assert repo.get_user_company("user-b", "1") is None
    assert repo.untrack_company("1", user_id="user-b") is False
    assert repo.untrack_company("1", user_id="user-a") is True
    assert repo.count_tracked_companies("user-a") == 0


def test_filing_index_idempotent(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    f = Filing(accession_number="a-1", cik="1", form_type="10-K", filed_at="2024-01-01")
    assert repo.upsert_filing(f) is True
    assert repo.upsert_filing(f) is False  # second insert ignored
    assert repo.known_accessions("1") == {"a-1"}


def test_prior_comparable_filing_is_independent_of_section_presence(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_filing(Filing(
        accession_number="old",
        cik="1",
        form_type="10-Q",
        filed_at="2024-01-01",
    ))
    repo.upsert_filing(Filing(
        accession_number="current",
        cik="1",
        form_type="10-Q",
        filed_at="2024-04-01",
    ))

    assert repo.has_prior_comparable_filing("1", "10-Q", "2024-04-01")
    assert repo.prior_comparable_section("1", "10-Q", "new_section", "2024-04-01") is None
    assert not repo.has_prior_comparable_filing("1", "10-K", "2024-04-01")


def test_filing_stage_progress_persists_attempts_and_diagnostics(repo):
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


def _attempt_trace(
    *,
    p1_id: int,
    p1_sha256: str,
    publication_outcome: str | None = None,
    verification_verdict: str | None = None,
    terminal_reason: str | None = None,
    trace_analysis_id: int | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": "harness.v2",
            "p1_analysis_id": p1_id,
            "trace_analysis_id": trace_analysis_id,
            "p1_output_sha256": p1_sha256,
            "publication_outcome": publication_outcome,
            "verification_verdict": verification_verdict,
            "terminal_reason": terminal_reason,
            "filing_snapshot": {
                "accession": "a-1",
                "ticker": "AAA",
                "form": "10-Q",
                "filed": "2025-01-01",
                "source_sha256": "a" * 64,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def test_p1_and_trace_insert_is_atomic_and_hashes_exact_stored_bytes(repo):
    p1 = Analysis(
        accession_number="a-1",
        ticker="AAA",
        stage="P1",
        model="m",
        prompt_version="p",
        output_json='{"unicode":"Δ","spacing": true}',
        created_at="t",
    )

    def trace_factory(p1_id: int, p1_sha256: str) -> Analysis:
        assert p1_sha256 == hashlib.sha256(p1.output_json.encode("utf-8")).hexdigest()
        return Analysis(
            accession_number="a-1",
            ticker="AAA",
            stage="P1_TRACE",
            model="m",
            prompt_version="p+s",
            output_json=_attempt_trace(p1_id=p1_id, p1_sha256=p1_sha256),
            created_at="t",
        )

    p1_id, trace_id, p1_sha256 = repo.insert_p1_with_trace(p1, trace_factory)

    assert repo.get_analysis(p1_id).output_json == p1.output_json
    assert repo.get_analysis(trace_id).stage == "P1_TRACE"
    assert p1_sha256 == hashlib.sha256(p1.output_json.encode("utf-8")).hexdigest()
    linked = repo.latest_linked_p1_attempt("a-1")
    assert linked is not None
    assert (linked[0].id, linked[1].id) == (p1_id, trace_id)


def test_latest_malformed_or_unlinked_trace_never_falls_back_to_an_old_p1(repo):
    p1 = Analysis(
        accession_number="a-1",
        ticker="AAA",
        stage="P1",
        model="m",
        prompt_version="p",
        output_json="{}",
        created_at="t",
    )

    def trace_factory(p1_id: int, p1_sha256: str) -> Analysis:
        return Analysis(
            accession_number="a-1",
            ticker="AAA",
            stage="P1_TRACE",
            model="m",
            prompt_version="p+s",
            output_json=_attempt_trace(p1_id=p1_id, p1_sha256=p1_sha256),
            created_at="t",
        )

    repo.insert_p1_with_trace(p1, trace_factory)
    assert repo.latest_linked_p1_attempt("a-1") is not None
    repo.insert_analysis(Analysis(
        accession_number="a-1",
        ticker="AAA",
        stage="P1_TRACE",
        model="m",
        prompt_version="p+s",
        output_json='{"schema_version":"harness.v2","p1_analysis_id":999}',
        created_at="t2",
    ))

    assert repo.latest_linked_p1_attempt("a-1") is None


def test_p1_and_trace_insert_rolls_back_when_second_insert_fails(repo):
    p1 = Analysis(
        accession_number="a-1",
        ticker="AAA",
        stage="P1",
        model="m",
        prompt_version="p",
        output_json="{}",
        created_at="t",
    )

    def broken_factory(p1_id: int, p1_sha256: str) -> Analysis:
        return Analysis.model_construct(
            accession_number="a-1",
            ticker="AAA",
            stage="P1_TRACE",
            model=None,
            prompt_version="p+s",
            output_json=_attempt_trace(p1_id=p1_id, p1_sha256=p1_sha256),
            created_at="t",
        )

    with pytest.raises(sqlite3.IntegrityError, match="analyses.model"):
        repo.insert_p1_with_trace(p1, broken_factory)

    assert repo.list_analyses("a-1") == []


def test_attempt_finalization_is_atomic_across_checks_trace_and_filing(repo):
    repo.upsert_company(Company(cik="1", ticker="AAA", added_at="t"))
    repo.upsert_filing(Filing(
        accession_number="a-1",
        cik="1",
        form_type="10-Q",
        filed_at="2025-01-01",
    ))
    p1 = Analysis(
        accession_number="a-1",
        ticker="AAA",
        stage="P1",
        model="m",
        prompt_version="p",
        output_json="{}",
        created_at="t",
    )

    def trace_factory(p1_id: int, p1_sha256: str) -> Analysis:
        return Analysis(
            accession_number="a-1",
            ticker="AAA",
            stage="P1_TRACE",
            model="m",
            prompt_version="p+s",
            output_json=_attempt_trace(p1_id=p1_id, p1_sha256=p1_sha256),
            created_at="t",
        )

    p1_id, trace_id, p1_sha256 = repo.insert_p1_with_trace(p1, trace_factory)
    initial_trace = repo.get_analysis(trace_id).output_json
    valid = VerificationResult(
        analysis_id=p1_id,
        check_id="V1",
        verdict="pass",
        severity="blocking",
        created_at="t2",
    )
    invalid = VerificationResult.model_construct(
        analysis_id=p1_id,
        check_id=None,
        verdict="pass",
        severity="blocking",
        detail=None,
        created_at="t2",
    )
    finalized = _attempt_trace(
        p1_id=p1_id,
        p1_sha256=p1_sha256,
        publication_outcome="published",
        verification_verdict="PASS",
        terminal_reason="verified",
        trace_analysis_id=trace_id,
    )

    with pytest.raises(sqlite3.IntegrityError, match="verification_results.check_id"):
        repo.finalize_p1_attempt(
            p1_id,
            trace_id,
            verification_results=[valid, invalid],
            finalized_trace_json=finalized,
            filing_status="verified",
            processed_at="t2",
        )

    assert repo.list_verification_results(p1_id) == []
    assert repo.get_analysis(trace_id).output_json == initial_trace
    stored_filing = repo.get_filing("a-1")
    assert stored_filing.status == "fetched"
    assert stored_filing.processed_at is None

    repo.finalize_p1_attempt(
        p1_id,
        trace_id,
        verification_results=[valid],
        finalized_trace_json=finalized,
        filing_status="verified",
        processed_at="t2",
    )
    assert [row.check_id for row in repo.list_verification_results(p1_id)] == ["V1"]
    assert repo.get_analysis(trace_id).output_json == finalized
    stored_filing = repo.get_filing("a-1")
    assert stored_filing.status == "verified"
    assert stored_filing.processed_at == "t2"


def test_settings_roundtrip(repo):
    assert repo.get_setting("period") is None
    repo.set_setting("period", "90d")
    assert repo.get_setting("period") == "90d"
    repo.set_setting("period", "1y")
    assert repo.get_setting("period") == "1y"


def _seed_linked_attempt(repo):
    p1 = Analysis(
        accession_number="a-1", ticker="AAA", stage="P1", model="m",
        prompt_version="p", output_json='{"findings": []}', created_at="t",
    )
    return repo.insert_p1_with_trace(p1, lambda p1_id, p1_sha256: Analysis(
        accession_number="a-1", ticker="AAA", stage="P1_TRACE", model="m",
        prompt_version="p+s",
        output_json=_attempt_trace(p1_id=p1_id, p1_sha256=p1_sha256), created_at="t",
    ))


def test_tampered_p1_bytes_break_the_attempt_link(repo):
    """The SHA-256 link is what binds a trace to the exact P1 bytes it describes.

    All three comparisons (insert, finalize, read) could be deleted with the whole
    suite still green, so the headline guarantee of the attempt-binding work had no
    executable guard.
    """
    p1_id, _trace_id, _sha = _seed_linked_attempt(repo)
    assert repo.latest_linked_p1_attempt("a-1") is not None

    repo.conn.execute(
        "UPDATE analyses SET output_json = ? WHERE id = ?",
        ('{"findings": [], "tampered": true}', p1_id),
    )
    repo.conn.commit()

    assert repo.latest_linked_p1_attempt("a-1") is None


def test_insert_rejects_a_trace_that_links_the_wrong_p1_bytes(repo):
    p1 = Analysis(
        accession_number="a-1", ticker="AAA", stage="P1", model="m",
        prompt_version="p", output_json='{"findings": []}', created_at="t",
    )
    with pytest.raises(ValueError, match="link"):
        repo.insert_p1_with_trace(p1, lambda p1_id, p1_sha256: Analysis(
            accession_number="a-1", ticker="AAA", stage="P1_TRACE", model="m",
            prompt_version="p+s",
            output_json=_attempt_trace(p1_id=p1_id, p1_sha256="0" * 64),
            created_at="t",
        ))


@pytest.mark.parametrize("payload", ["null", "[]", '"text"', "7", "{not json"])
def test_malformed_trace_payload_fails_closed_instead_of_raising(repo, payload):
    """A trace payload that parses to a JSON scalar or array is still malformed state.

    Dereferencing it raised AttributeError out of the selector, which 500s the filing,
    certificate AND brief endpoints — one corrupt row denying a whole workspace rather
    than withholding one filing.
    """
    _seed_linked_attempt(repo)
    trace_row = repo.latest_analysis("a-1", "P1_TRACE")
    repo.conn.execute(
        "UPDATE analyses SET output_json = ? WHERE id = ?", (payload, trace_row.id)
    )
    repo.conn.commit()

    assert repo.latest_linked_p1_attempt("a-1") is None
