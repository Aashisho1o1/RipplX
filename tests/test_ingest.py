"""Ingestion service — the Phase 1 DoD: add + ingest populates every table."""
from __future__ import annotations

from datetime import date

import pytest

from finwatch.ingest.service import TickerNotFoundError, companyfacts_to_rows

CIK = "0000320193"


def test_add_then_ingest_populates_launch_tables(ingest_service):
    svc, repo = ingest_service, ingest_service.repo
    svc.add_holding("aapl")

    summary = svc.ingest_all(backfill_quarters=8)
    assert summary.companies == 1
    assert summary.results[0].error is None

    company = repo.get_company(CIK)
    assert company.ticker == "AAPL" and company.name == "Apple Inc."
    assert company.sic_code == "3571" and company.sector_class == "general"
    assert company.is_financial == 0

    filings = repo.list_filings(CIK)
    assert len(filings) == 3
    assert filings[0].primary_doc_url.endswith("aapl-20240928.htm")

    assert repo.count_xbrl_facts(CIK) == 7
def test_add_unknown_ticker_raises(ingest_service):
    with pytest.raises(TickerNotFoundError):
        ingest_service.add_holding("ZZZZ")


def test_ingest_is_idempotent(ingest_service):
    svc = ingest_service
    svc.add_holding("AAPL")
    svc.ingest_all()
    again = svc.ingest_all()
    assert again.filings_new == 0
    assert svc.repo.count_xbrl_facts(CIK) == 7  # replace, not duplicate


def test_backfill_cutoff_excludes_old_filings(ingest_service):
    # as_of 2024-12-01; 4 quarters ≈ 365d -> cutoff ~2023-12-02, excludes 2023-11-03 10-K
    svc = ingest_service
    svc.add_holding("AAPL")
    summary = svc.ingest_all(backfill_quarters=4)
    assert summary.filings == 2


def test_backfill_none_indexes_all(ingest_service):
    svc = ingest_service
    svc.add_holding("AAPL")
    summary = svc.ingest_all(backfill_quarters=None)
    assert summary.filings == 3


def test_amendment_flag_detected(ingest_service):
    subs = {"filings": {"recent": {
        "accessionNumber": ["x-1", "x-2"],
        "form": ["10-K", "10-K/A"],
        "filingDate": ["2024-01-01", "2024-02-01"],
        "reportDate": ["2023-12-31", "2023-12-31"],
        "primaryDocument": ["a.htm", "b.htm"],
    }}}
    indexed, new = ingest_service._index_filings("0000000009", subs, None)
    assert (indexed, new) == (2, 2)
    assert ingest_service.repo.get_filing("x-1").is_amendment == 0
    assert ingest_service.repo.get_filing("x-2").is_amendment == 1


def test_index_filings_reads_paginated_files_pages(ingest_service):
    # 'recent' holds the newest filing; an older but in-window 10-Q lives on a 'files'
    # page. The indexer must fetch in-window pages and skip pages older than the window.
    subs = {"filings": {
        "recent": {
            "accessionNumber": ["r-1"], "form": ["8-K"], "filingDate": ["2024-06-01"],
            "reportDate": ["2024-06-01"], "primaryDocument": ["r.htm"]},
        "files": [
            {"name": "CIK0000000009-submissions-001.json",
             "filingFrom": "2023-01-01", "filingTo": "2024-01-31"},   # overlaps window
            {"name": "CIK0000000009-submissions-002.json",
             "filingFrom": "2010-01-01", "filingTo": "2011-12-31"},   # predates window
        ]}}
    page_001 = {"accessionNumber": ["p-1"], "form": ["10-Q"], "filingDate": ["2024-01-15"],
                "reportDate": ["2023-12-31"], "primaryDocument": ["p.htm"]}
    fetched: list[str] = []

    def fake_page(name):
        fetched.append(name)
        return page_001 if name.endswith("001.json") else {}

    ingest_service.edgar.submissions_page = fake_page
    indexed, new = ingest_service._index_filings("0000000009", subs, backfill_quarters=8)

    assert new == 2                                              # r-1 (recent) + p-1 (page 001)
    assert ingest_service.repo.get_filing("p-1").form_type == "10-Q"
    assert fetched == ["CIK0000000009-submissions-001.json"]     # out-of-window page not fetched


def test_companyfacts_to_rows_splits_instant_vs_duration():
    cf = {"facts": {"us-gaap": {
        "Assets": {"units": {"USD": [
            {"end": "2024-01-01", "val": 100, "decimals": -6, "fy": 2024, "fp": "FY",
             "form": "10-K", "accn": "a"}]}},
        "Revenues": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2024-01-01", "val": 50, "fy": 2024, "fp": "FY",
             "form": "10-K", "accn": "a"}]}},
    }}}
    rows = {r.tag: r for r in companyfacts_to_rows(cf, "0000000001")}
    assert rows["Assets"].instant == "2024-01-01"
    assert rows["Assets"].period_end is None and rows["Assets"].period_start is None
    assert rows["Assets"].decimals == "-6"
    assert rows["Revenues"].period_start == "2023-01-01"
    assert rows["Revenues"].period_end == "2024-01-01"
    assert rows["Revenues"].instant is None


def test_companyfacts_to_rows_skips_null_values():
    cf = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"end": "2024-01-01", "val": None, "accn": "a"},
        {"end": "2024-02-01", "val": 5, "accn": "a"},
    ]}}}}}
    rows = companyfacts_to_rows(cf, "1")
    assert len(rows) == 1 and rows[0].value == 5.0


def test_ingest_error_isolation_across_ciks(ingest_service):
    # AAPL has fixtures; MSFT does not -> its submissions fail. The batch must
    # still complete and ingest AAPL fully.
    svc = ingest_service
    svc.add_holding("AAPL")
    svc.add_holding("MSFT")
    summary = svc.ingest_all()
    assert summary.companies == 2
    by = {r.ticker: r for r in summary.results}
    assert by["AAPL"].error is None
    assert by["AAPL"].xbrl_facts == 7
    assert by["MSFT"].error is not None and "submissions/filings" in by["MSFT"].error


def test_empty_companyfacts_does_not_wipe_history(ingest_service):
    svc = ingest_service
    svc.add_holding("AAPL")
    svc.ingest_all()
    assert svc.repo.count_xbrl_facts(CIK) == 7
    # an anomalous but valid (HTTP 200) payload with no facts must NOT erase history
    svc.edgar.companyfacts = lambda cik, **kw: {"facts": {}}
    assert svc._ingest_companyfacts(CIK) == 7
    assert svc.repo.count_xbrl_facts(CIK) == 7


def test_companyfacts_404_yields_zero_facts_not_error(repo):
    import httpx

    from finwatch.db import Company, Holding
    from finwatch.ingest import EdgarClient, IngestService

    repo.upsert_company(Company(cik="0000000123", ticker="XYZ", added_at="t"))
    repo.upsert_holding(Holding(cik="0000000123", ticker="XYZ", owned=0, added_at="t"))

    def handler(req):
        u = str(req.url)
        if "submissions/CIK0000000123" in u:
            return httpx.Response(200, json={"name": "XYZ Co", "sic": "7372", "filings": {
                "recent": {"accessionNumber": ["a-1"], "form": ["10-K"],
                           "filingDate": ["2024-01-01"], "reportDate": ["2023-12-31"],
                           "primaryDocument": ["x.htm"]}}})
        if "companyfacts/CIK0000000123" in u:
            return httpx.Response(404)  # foreign filer / no structured XBRL
        return httpx.Response(404)

    mock = httpx.MockTransport(handler)
    svc = IngestService(
        repo,
        EdgarClient("UA t@e.com", client=httpx.Client(transport=mock), sleep=lambda _s: None),
        as_of=date(2024, 12, 1),
    )
    summary = svc.ingest_all(backfill_quarters=None)
    r = summary.results[0]
    assert r.error is None
    assert r.xbrl_facts == 0  # 404 -> zero, not a failure
    assert r.filings_indexed == 1
    assert repo.count_xbrl_facts("0000000123") == 0
