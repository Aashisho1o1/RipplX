"""Instrument classification and symbol normalization (pure, no I/O)."""
from __future__ import annotations

import pytest

from finwatch.broker import (
    MAX_PASTED_SYMBOLS,
    BrokerPosition,
    is_trackable_instrument,
    normalize_broker_symbol,
    parse_symbol_list,
)


def _position(symbol: str, **overrides) -> BrokerPosition:
    fields = {
        "symbol": symbol,
        "instrument_kind": "cs",
        "exchange_mic": "XNAS",
        "currency": "USD",
    }
    fields.update(overrides)
    return BrokerPosition(**fields)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BRK.B", "BRK-B"),     # brokers write dots, SEC writes hyphens
        ("BRK/B", "BRK-B"),
        ("BRK-B", "BRK-B"),
        ("brk.b", "BRK-B"),
        ("  AAPL  ", "AAPL"),
        ("GOOGL", "GOOGL"),
        # A single-character suffix is left for the SEC index to judge. If BRK-B or
        # XYZ-U is in company_tickers.json it is a real registrant symbol; if it is
        # not, resolution reports not_found. Normalization is not the second gate.
        ("XYZ.U", "XYZ-U"),
    ],
)
def test_share_class_separators_are_rewritten_to_the_sec_spelling(raw, expected):
    assert normalize_broker_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["VAB.TO", "ABC.WS", "", "   ", "1AAPL", "A B"])
def test_symbols_that_are_not_a_us_common_stock_listing_are_rejected(raw):
    """A multi-character suffix is rejected, never stripped.

    Stripping ``.TO`` off ``VAB.TO`` would silently produce ``VAB`` — a different
    company's ticker. Refusing to guess is the whole point.
    """
    assert normalize_broker_symbol(raw) is None


def test_only_us_listed_common_stock_is_trackable():
    assert is_trackable_instrument(_position("AAPL")) is True
    assert is_trackable_instrument(_position("AAPL", exchange_mic="XNYS")) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"instrument_kind": "etf"},
        {"instrument_kind": "mutual_fund"},
        {"instrument_kind": "crypto"},
        {"instrument_kind": "option"},
        {"instrument_kind": "bond"},
        {"instrument_kind": "cash"},
        {"instrument_kind": "equity"},      # an ETF is an equity — too broad to admit
        {"instrument_kind": None},          # missing metadata is not an invitation
        {"instrument_kind": "totally-new"},  # unknown kinds fail closed
        {"currency": "CAD"},
        {"currency": None},
        {"exchange_mic": "XTSE"},           # Toronto, not a US listing
        {"exchange_mic": None},
    ],
)
def test_anything_not_recognised_as_us_common_stock_fails_closed(overrides):
    assert is_trackable_instrument(_position("AAPL", **overrides)) is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("AAPL,MSFT", ["AAPL", "MSFT"]),
        ("AAPL\nMSFT\nJPM", ["AAPL", "MSFT", "JPM"]),
        ("aapl, msft;jpm|brk.b", ["AAPL", "MSFT", "JPM", "BRK.B"]),
        ("  AAPL  ,, \n\n MSFT ", ["AAPL", "MSFT"]),
        ("AAPL, AAPL, aapl", ["AAPL"]),        # de-duplicated, order preserved
        ("", []),
        ("   \n  ", []),
    ],
)
def test_pasted_text_splits_on_whatever_separators_people_actually_use(text, expected):
    assert parse_symbol_list(text) == expected


def test_a_large_paste_is_bounded():
    """One request must not turn into an unbounded amount of resolution work."""
    text = ",".join(f"SYM{index}" for index in range(500))

    assert len(parse_symbol_list(text)) == MAX_PASTED_SYMBOLS
    assert len(parse_symbol_list(text, limit=5)) == 5
