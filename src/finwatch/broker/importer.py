"""Plan and apply a brokerage ticker import.

Split in two on purpose. :func:`plan_import` is pure — it classifies and resolves with
no database and no network, so it can be shown to the user for confirmation before
anything is written. :func:`apply_plan` performs the writes. That split is what lets a
caller offer a choice when an account has more eligible issuers than the workspace cap,
rather than silently taking an arbitrary subset.

Every submitted symbol appears exactly once in the result with a fixed outcome code.
Nothing is dropped silently: an account that is mostly ETFs must be visibly mostly
skipped, not quietly half-imported.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from finwatch.broker.symbols import (
    BrokerPosition,
    is_trackable_instrument,
    normalize_broker_symbol,
)
from finwatch.db.repositories import (
    LOCAL_USER_ID,
    Company,
    Repo,
    TickerIdentityConflictError,
)
from finwatch.ingest.tickers import TickerRecord

Outcome = Literal[
    "tracked",
    "already_tracked",
    "unsupported_instrument",
    "not_found",
    "ticker_identity_conflict",
    "skipped_cap",
]


class Candidate(BaseModel):
    """One issuer to track, with every broker symbol that resolved to it."""

    symbol: str
    cik: str
    ticker: str
    aliases: list[str] = Field(default_factory=list)


class ImportRow(BaseModel):
    symbol: str
    outcome: Outcome
    ticker: str | None = None


class ImportPlan(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    rejected: list[ImportRow] = Field(default_factory=list)

    @property
    def eligible_count(self) -> int:
        return len(self.candidates)


class ImportResult(BaseModel):
    rows: list[ImportRow] = Field(default_factory=list)
    tracked_count: int = 0
    cap_reached: bool = False


class _Planner:
    """Shared resolve-and-dedupe pass behind both entry points."""

    def __init__(self, index: dict[str, TickerRecord]) -> None:
        self._index = index
        self._candidates: dict[str, Candidate] = {}
        self._rejected: list[ImportRow] = []
        self._order: dict[str, int] = {}
        self._seen: set[str] = set()

    def accept(self, raw: str) -> str | None:
        """Register a symbol for this batch, or None when it is a repeat."""
        symbol = (raw or "").strip().upper()
        if not symbol or symbol in self._seen:
            return None  # one row per distinct symbol; subaccounts may repeat a holding
        self._seen.add(symbol)
        self._order.setdefault(symbol, len(self._order))
        return symbol

    def reject(self, symbol: str, outcome: Outcome) -> None:
        self._rejected.append(ImportRow(symbol=symbol, outcome=outcome))

    def resolve(self, symbol: str) -> None:
        normalized = normalize_broker_symbol(symbol)
        if normalized is None:
            self.reject(symbol, "not_found")
            return
        record = self._index.get(normalized)
        if record is None:
            self.reject(symbol, "not_found")
            return
        existing = self._candidates.get(record.cik)
        if existing is None:
            self._candidates[record.cik] = Candidate(
                symbol=symbol, cik=record.cik, ticker=record.ticker
            )
        else:
            existing.aliases.append(symbol)
            # Mirror the persistence rule so the reported label matches what is stored.
            existing.ticker = min(existing.ticker, record.ticker)

    def plan(self) -> ImportPlan:
        return ImportPlan(
            candidates=sorted(
                self._candidates.values(), key=lambda c: self._order[c.symbol]
            ),
            rejected=sorted(self._rejected, key=lambda row: self._order[row.symbol]),
        )


def plan_symbols(symbols: list[str], index: dict[str, TickerRecord]) -> ImportPlan:
    """Plan an import from bare symbols the user supplied directly.

    There is no instrument metadata to classify on, so resolution against the SEC ticker
    index is the only gate — the same gate a hand-typed ticker already passes through.
    An unresolvable symbol is ``not_found``, which covers both a typo and an instrument
    with no SEC registrant.
    """
    planner = _Planner(index)
    for raw in symbols:
        symbol = planner.accept(raw)
        if symbol is not None:
            planner.resolve(symbol)
    return planner.plan()


def plan_import(
    positions: list[BrokerPosition], index: dict[str, TickerRecord]
) -> ImportPlan:
    """Classify, normalize, and resolve aggregator positions.

    ``index`` is built once for the whole batch rather than per symbol, so a 40-position
    account costs one pass over ``company_tickers.json`` instead of forty.

    Unlike :func:`plan_symbols`, positions carry structured instrument metadata, so the
    stricter instrument gate applies before resolution — which is what keeps a crypto
    ``ETH`` from resolving against an unrelated equity.

    Candidates are deduped **by CIK**, not by symbol: share classes are one issuer with
    one filing history, so GOOG and GOOGL collapse to a single watchlist entry and the
    second symbol is reported as an alias of the first.
    """
    planner = _Planner(index)
    for position in positions:
        symbol = planner.accept(position.symbol)
        if symbol is None:
            continue
        if not is_trackable_instrument(position):
            planner.reject(symbol, "unsupported_instrument")
            continue
        if normalize_broker_symbol(symbol) is None:
            planner.reject(symbol, "unsupported_instrument")
            continue
        planner.resolve(symbol)
    return planner.plan()


def apply_plan(
    repo: Repo,
    plan: ImportPlan,
    *,
    user_id: str = LOCAL_USER_ID,
    cap: int,
    now: str,
) -> ImportResult:
    """Track each candidate, filling to ``cap`` and reporting the remainder.

    Reaching the cap is never a batch failure: the issuers that fit are tracked and the
    rest come back as ``skipped_cap``, so a 40-position account against a 25-ticker
    workspace still delivers 25 and says so. A symbol whose issuer collides with a stale
    row fails on its own without taking the batch down.
    """
    rows: list[ImportRow] = list(plan.rejected)
    tracked = 0
    cap_reached = False
    slots = max(0, cap - repo.count_tracked_companies(user_id))

    for candidate in plan.candidates:
        label = candidate.ticker
        if repo.get_user_company(user_id, candidate.cik) is not None:
            outcome: Outcome = "already_tracked"
        elif slots <= 0:
            outcome = "skipped_cap"
            cap_reached = True
        else:
            try:
                repo.upsert_company(Company(
                    cik=candidate.cik, ticker=candidate.ticker, added_at=now,
                ))
            except TickerIdentityConflictError:
                outcome = "ticker_identity_conflict"
            else:
                repo.track_company(candidate.cik, at=now, user_id=user_id)
                outcome = "tracked"
                tracked += 1
                slots -= 1

        if outcome in {"tracked", "already_tracked"}:
            stored = repo.get_company(candidate.cik)
            if stored is not None:
                label = stored.ticker

        reported = label if outcome != "ticker_identity_conflict" else None
        rows.append(ImportRow(symbol=candidate.symbol, outcome=outcome, ticker=reported))
        rows.extend(
            ImportRow(symbol=alias, outcome=outcome, ticker=reported)
            for alias in candidate.aliases
        )

    return ImportResult(rows=rows, tracked_count=tracked, cap_reached=cap_reached)


__all__ = [
    "Candidate",
    "ImportPlan",
    "ImportResult",
    "ImportRow",
    "Outcome",
    "apply_plan",
    "plan_import",
    "plan_symbols",
]
