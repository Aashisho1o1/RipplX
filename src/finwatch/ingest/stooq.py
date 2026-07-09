"""Stooq EOD price fetch.

Stooq's free daily CSV endpoint (no key) returns ``Date,Open,High,Low,Close,Volume``
with ISO dates. Ingest stores full history in the ``prices`` table; the ``Repo``
class then satisfies the ``PriceProvider`` protocol in metrics/formulas.py via
``Repo.close_on_or_before`` (a pure DB lookup — no network at metrics time).
"""
from __future__ import annotations

import httpx

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


def stooq_symbol(ticker: str) -> str:
    """US equities on Stooq use the ``<ticker>.us`` symbol form."""
    return f"{ticker.strip().lower()}.us"


def parse_stooq_csv(text: str) -> list[tuple[str, float]]:
    """Parse a Stooq daily CSV into ``[(date_iso, close), ...]``.

    Tolerant of missing values (``N/D``) and short/blank rows; returns [] when the
    payload is empty or lacks the expected header (e.g. an unknown symbol page).
    """
    lines = text.strip().splitlines()
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(",")]
    try:
        i_date = header.index("Date")
        i_close = header.index("Close")
    except ValueError:
        return []
    out: list[tuple[str, float]] = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(i_date, i_close):
            continue
        d, c = parts[i_date].strip(), parts[i_close].strip()
        if not d or c in ("", "N/D"):
            continue
        try:
            close = float(c)
        except ValueError:
            continue
        out.append((d, close))
    return out


class StooqClient:
    """Fetch daily close history from Stooq. httpx client injectable for tests."""

    def __init__(self, *, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._client = client or httpx.Client(timeout=timeout)

    def fetch_history(self, ticker: str) -> list[tuple[str, float]]:
        url = STOOQ_URL.format(symbol=stooq_symbol(ticker))
        resp = self._client.get(url)
        resp.raise_for_status()
        return parse_stooq_csv(resp.text)

    def close(self) -> None:
        self._client.close()
