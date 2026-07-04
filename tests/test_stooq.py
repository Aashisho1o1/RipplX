"""Stooq CSV parsing, client, and the DB-backed price provider."""
from __future__ import annotations

from pathlib import Path

import httpx

from finwatch.db import Price, Repo, init_db
from finwatch.ingest.stooq import (
    StooqClient,
    StooqPriceProvider,
    parse_stooq_csv,
    stooq_symbol,
)

STOOQ_CSV = (Path(__file__).parent / "fixtures" / "stooq_aapl.csv").read_text(encoding="utf-8")


def test_symbol():
    assert stooq_symbol("AAPL") == "aapl.us"
    assert stooq_symbol(" msft ") == "msft.us"


def test_parse_csv_basic():
    txt = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-01-02,1,2,0.5,1.5,100\n"
        "2024-01-03,2,3,1,N/D,\n"      # missing close -> skipped
        "2024-01-04,2,3,1,2.5,200\n"
    )
    assert parse_stooq_csv(txt) == [("2024-01-02", 1.5), ("2024-01-04", 2.5)]


def test_parse_csv_empty_or_headerless():
    assert parse_stooq_csv("") == []
    assert parse_stooq_csv("garbage line\n1,2,3") == []


def test_client_fetch_parses_fixture():
    def handler(req):
        return httpx.Response(200, text=STOOQ_CSV)

    client = StooqClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    rows = client.fetch_history("AAPL")
    assert rows[0] == ("2024-09-27", 227.79)
    assert rows[-1] == ("2024-11-01", 222.91)
    assert len(rows) == 3


def test_price_provider_reads_db():
    conn = init_db(":memory:")
    repo = Repo(conn)
    repo.upsert_prices([
        Price(ticker="AAA", date="2024-01-02", close=10.0),
        Price(ticker="AAA", date="2024-01-05", close=11.0),
    ])
    pp = StooqPriceProvider(repo)
    assert pp.close_on_or_before("AAA", "2024-01-06") == 11.0
    assert pp.close_on_or_before("AAA", "2024-01-03") == 10.0
    assert pp.close_on_or_before("AAA", "2024-01-01") is None
