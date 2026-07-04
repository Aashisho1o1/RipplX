"""ticker→CIK resolution from SEC ``company_tickers.json``.

The master file is a dict keyed by row index, each value
``{"cik_str": int, "ticker": str, "title": str}``. We build a case-insensitive
ticker index of 10-digit CIKs.
"""
from __future__ import annotations

from pydantic import BaseModel

from finwatch.ingest.edgar import normalize_cik


class TickerRecord(BaseModel):
    cik: str
    ticker: str
    title: str | None = None


def build_ticker_index(company_tickers_json: dict) -> dict[str, TickerRecord]:
    index: dict[str, TickerRecord] = {}
    for entry in company_tickers_json.values():
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker", "")).strip().upper()
        cik_raw = entry.get("cik_str")
        if not ticker or cik_raw is None:
            continue
        index[ticker] = TickerRecord(
            cik=normalize_cik(cik_raw), ticker=ticker, title=entry.get("title")
        )
    return index


def resolve_ticker(company_tickers_json: dict, ticker: str) -> TickerRecord | None:
    return build_ticker_index(company_tickers_json).get(ticker.strip().upper())
