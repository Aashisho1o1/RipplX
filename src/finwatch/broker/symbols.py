"""Instrument classification and symbol normalization — pure, no I/O.

Two rules govern this module.

**Classify before resolving.** A crypto position in ``ETH`` or ``SOL`` will happily
resolve against an unrelated equity in the SEC ticker index and start showing the user
filings for a company they do not own. Instrument kind is decided from the aggregator's
*structured* fields, never from the symbol string and never from a human-readable label.

**Default deny.** An unrecognised kind, a missing exchange, or a non-US currency is
``unsupported``, not ``tracked``. RipplX only reasons about issuers that file
10-K/10-Q/8-K; everything else — ETFs, funds, ADRs, crypto, bonds, options, cash — is
one honest "we can't watch this" outcome rather than a silently inert watchlist row.
"""

from __future__ import annotations

import re

from pydantic import BaseModel


class BrokerPosition(BaseModel):
    """One position, reduced to identity at the network boundary.

    Deliberately has no quantity, cost basis, market value, or account field: the
    ticker-only promise is enforced by the type, so no later code can persist what was
    never carried.
    """

    symbol: str
    instrument_kind: str | None = None
    exchange_mic: str | None = None
    currency: str | None = None


# Structured instrument kinds that denote ordinary listed common stock. Anything absent
# from this set is unsupported. Broad tokens like "equity" are deliberately excluded:
# an ETF is an equity, and admitting one would put an untrackable row on the watchlist.
# The exact tokens a provider emits are confirmed in the vendor spike before enabling
# any live connection; widening this set is a trust-relevant change.
COMMON_STOCK_KINDS = frozenset({"cs", "common_stock", "commonstock", "common stock"})

# US listing venues. A position without a recognised US venue cannot be assumed to be an
# SEC registrant, so it is unsupported rather than guessed.
US_EXCHANGE_MICS = frozenset({
    "XNYS", "XNAS", "XASE", "ARCX", "BATS", "BATY", "EDGA", "EDGX", "IEXG", "XCIS",
    "XNCM", "XNGS", "XNMS", "AMXO", "NYSE", "NASDAQ",
})

_VALID_SEC_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)?$")


def is_trackable_instrument(position: BrokerPosition) -> bool:
    """True only for ordinary US-listed common stock."""
    kind = (position.instrument_kind or "").strip().lower()
    if kind not in COMMON_STOCK_KINDS:
        return False
    if (position.currency or "").strip().upper() != "USD":
        return False
    return (position.exchange_mic or "").strip().upper() in US_EXCHANGE_MICS


def normalize_broker_symbol(raw: str) -> str | None:
    """Map a broker symbol onto SEC's spelling, or None when it is not one.

    SEC writes share classes with a hyphen (``BRK-B``); brokers and aggregators write
    ``BRK.B``, ``BRK/B``, or append an exchange suffix (``VAB.TO``). Only the share-class
    separator is rewritten — a trailing exchange suffix means the listing is not the US
    one, so the symbol is rejected rather than silently stripped into a different
    company's ticker.
    """
    symbol = (raw or "").strip().upper()
    if not symbol:
        return None
    symbol = symbol.replace("/", "-").replace(".", "-")
    if not _VALID_SEC_SYMBOL.match(symbol):
        return None
    head, _, tail = symbol.partition("-")
    # A share class is a short suffix (BRK-B, BF-B). A longer one is an exchange or
    # instrument suffix (VAB-TO, ABC-WS) and is not a US common-stock listing.
    if tail and len(tail) > 1:
        return None
    if not head:
        return None
    return symbol


MAX_PASTED_SYMBOLS = 100

_SEPARATORS = re.compile(r"[\s,;|]+")


def parse_symbol_list(text: str, *, limit: int = MAX_PASTED_SYMBOLS) -> list[str]:
    """Split pasted text into candidate symbols, in order, without duplicates.

    Accepts whatever separators a person actually pastes — commas, newlines, tabs,
    semicolons, pipes. Nothing is validated here; a nonsense token simply fails to
    resolve later and is reported as ``not_found``, which is more useful than rejecting
    the whole paste over one bad line.

    ``limit`` bounds the batch so a large paste cannot turn one request into an
    unbounded amount of work.
    """
    symbols: list[str] = []
    seen: set[str] = set()
    for token in _SEPARATORS.split(text or ""):
        symbol = token.strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
        if len(symbols) >= limit:
            break
    return symbols


__all__ = [
    "COMMON_STOCK_KINDS",
    "MAX_PASTED_SYMBOLS",
    "US_EXCHANGE_MICS",
    "BrokerPosition",
    "is_trackable_instrument",
    "normalize_broker_symbol",
    "parse_symbol_list",
]
