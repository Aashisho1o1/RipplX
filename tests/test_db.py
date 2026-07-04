"""Data layer: schema/migrations + repository semantics."""
from __future__ import annotations

from finwatch.db import Company, Filing, Holding, Price, XbrlFact, apply_migrations, init_db
from finwatch.db.database import SCHEMA_VERSION


def test_schema_applies_and_versions():
    conn = init_db(":memory:")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "companies", "holdings", "filings", "filing_sections", "xbrl_facts", "prices",
        "analyses", "analysis_claims", "computations", "verification_results",
        "signal_shadow_log", "digests", "settings",
    } <= tables


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


def test_replace_xbrl_facts_replaces_not_appends(repo):
    facts = [XbrlFact(cik="1", taxonomy="us-gaap", tag="Assets", value=100.0, instant="2024-01-01")]
    assert repo.replace_xbrl_facts("1", facts) == 1
    assert repo.replace_xbrl_facts("1", facts + facts) == 2
    assert repo.count_xbrl_facts("1") == 2  # prior single row was deleted first


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
