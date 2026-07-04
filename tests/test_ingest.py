"""Ingestion service — the Phase 1 DoD: add + ingest populates every table."""
from __future__ import annotations

from datetime import date

import pytest

from finwatch.ingest.service import TickerNotFoundError, companyfacts_to_rows

CIK = "0000320193"


def test_add_then_ingest_populates_all_tables(ingest_service):
    svc, repo = ingest_service, ingest_service.repo
    svc.add_holding("aapl", owned=True, shares=10, cost_basis=150, thesis="ecosystem moat")

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
    assert repo.count_prices("AAPL") == 3
    assert repo.close_on_or_before("AAPL", "2024-11-05") == 222.91


def test_add_unknown_ticker_raises(ingest_service):
    with pytest.raises(TickerNotFoundError):
        ingest_service.add_holding("ZZZZ", owned=True)


def test_watch_registers_unowned(ingest_service):
    ingest_service.add_holding("AAPL", owned=False)
    holding = ingest_service.repo.get_holding_by_cik(CIK)
    assert holding.owned == 0 and holding.shares is None


def test_ingest_is_idempotent(ingest_service):
    svc = ingest_service
    svc.add_holding("AAPL", owned=False)
    svc.ingest_all()
    again = svc.ingest_all()
    assert again.filings_new == 0
    assert svc.repo.count_xbrl_facts(CIK) == 7  # replace, not duplicate


def test_backfill_cutoff_excludes_old_filings(ingest_service):
    # as_of 2024-12-01; 4 quarters ≈ 365d -> cutoff ~2023-12-02, excludes 2023-11-03 10-K
    svc = ingest_service
    svc.add_holding("AAPL", owned=False)
    summary = svc.ingest_all(backfill_quarters=4)
    assert summary.filings == 2


def test_backfill_none_indexes_all(ingest_service):
    svc = ingest_service
    svc.add_holding("AAPL", owned=False)
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
    # AAPL has fixtures; MSFT does not -> its submissions/prices fail. The batch must
    # still complete and ingest AAPL fully.
    svc = ingest_service
    svc.add_holding("AAPL", owned=False)
    svc.add_holding("MSFT", owned=False)
    summary = svc.ingest_all()
    assert summary.companies == 2
    by = {r.ticker: r for r in summary.results}
    assert by["AAPL"].error is None
    assert by["AAPL"].xbrl_facts == 7 and by["AAPL"].prices == 3
    assert by["MSFT"].error is not None and "submissions/filings" in by["MSFT"].error
    assert svc.repo.count_prices("AAPL") == 3  # good CIK unaffected by bad CIK


def test_empty_companyfacts_does_not_wipe_history(ingest_service):
    svc = ingest_service
    svc.add_holding("AAPL", owned=False)
    svc.ingest_all()
    assert svc.repo.count_xbrl_facts(CIK) == 7
    # an anomalous but valid (HTTP 200) payload with no facts must NOT erase history
    svc.edgar.companyfacts = lambda cik, **kw: {"facts": {}}
    assert svc._ingest_companyfacts(CIK) == 7
    assert svc.repo.count_xbrl_facts(CIK) == 7


def test_companyfacts_404_yields_zero_facts_not_error(repo):
    import httpx

    from finwatch.db import Company, Holding
    from finwatch.ingest import EdgarClient, IngestService, StooqClient

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
        if "stooq.com" in u and "s=xyz.us" in u:
            csv = "Date,Open,High,Low,Close,Volume\n2024-01-02,1,1,1,1.5,10\n"
            return httpx.Response(200, text=csv)
        return httpx.Response(404)

    mock = httpx.MockTransport(handler)
    svc = IngestService(
        repo,
        EdgarClient("UA t@e.com", client=httpx.Client(transport=mock), sleep=lambda _s: None),
        StooqClient(client=httpx.Client(transport=mock)),
        as_of=date(2024, 12, 1),
    )
    summary = svc.ingest_all(backfill_quarters=None)
    r = summary.results[0]
    assert r.error is None
    assert r.xbrl_facts == 0  # 404 -> zero, not a failure
    assert r.filings_indexed == 1 and r.prices == 1
    assert repo.count_xbrl_facts("0000000123") == 0
