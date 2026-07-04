"""ticker→CIK resolution."""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.ingest.tickers import build_ticker_index, resolve_ticker

TICKERS = json.loads(
    (Path(__file__).parent / "fixtures" / "company_tickers.json").read_text(encoding="utf-8")
)


def test_resolve_case_insensitive():
    rec = resolve_ticker(TICKERS, "aapl")
    assert rec is not None
    assert rec.cik == "0000320193"
    assert rec.ticker == "AAPL"
    assert rec.title == "Apple Inc."


def test_resolve_missing_returns_none():
    assert resolve_ticker(TICKERS, "ZZZZ") is None


def test_index_zero_pads_cik():
    idx = build_ticker_index(TICKERS)
    assert idx["JPM"].cik == "0000019617"  # 19617 -> 10 digits
    assert idx["MSFT"].cik == "0000789019"


def test_index_skips_malformed_entries():
    raw = {
        "0": {"cik_str": 1, "ticker": "AAA", "title": "A"},
        "1": {"ticker": "NOCIK"},          # missing cik_str
        "2": {"cik_str": 2},               # missing ticker
        "3": "garbage",
    }
    idx = build_ticker_index(raw)
    assert set(idx) == {"AAA"}
