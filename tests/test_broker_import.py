"""Brokerage ticker import: planning is pure, applying respects the workspace cap."""
from __future__ import annotations

from finwatch.broker import BrokerPosition, apply_plan, plan_import
from finwatch.db.repositories import Company
from finwatch.ingest.tickers import build_ticker_index

ALPHABET = "0001652044"

# A deliberately hostile index: ETH is a real listed equity here, so a crypto position
# in ETH would resolve if the importer ever classified after resolving.
INDEX = build_ticker_index({
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
    "3": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "4": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    "5": {"cik_str": 5981234, "ticker": "ETH", "title": "Some Listed Company Inc."},
})


def _stock(symbol: str, **overrides) -> BrokerPosition:
    fields = {
        "symbol": symbol,
        "instrument_kind": "cs",
        "exchange_mic": "XNAS",
        "currency": "USD",
    }
    fields.update(overrides)
    return BrokerPosition(**fields)


def _outcomes(result) -> dict[str, str]:
    return {row.symbol: row.outcome for row in result.rows}


def test_a_realistic_mixed_account_reports_every_symbol_exactly_once(repo):
    positions = [
        _stock("AAPL"),
        _stock("SPY", instrument_kind="etf"),
        _stock("BRK.B", exchange_mic="XNYS"),
        _stock("ETH", instrument_kind="crypto"),
        _stock("VMFXX", instrument_kind="mutual_fund"),
        _stock("NOSUCH"),
        _stock("USD", instrument_kind="cash"),
    ]

    result = apply_plan(
        repo, plan_import(positions, INDEX), cap=25, now="t"
    )

    assert _outcomes(result) == {
        "AAPL": "tracked",
        "BRK.B": "tracked",              # normalized to BRK-B, then resolved
        "SPY": "unsupported_instrument",
        "ETH": "unsupported_instrument",
        "VMFXX": "unsupported_instrument",
        "USD": "unsupported_instrument",
        "NOSUCH": "not_found",
    }
    assert result.tracked_count == 2
    assert len(result.rows) == len(positions)   # nothing dropped silently
    assert sorted(repo.list_tracked_ciks()) == ["0000320193", "0001067983"]


def test_a_crypto_symbol_is_classified_before_it_can_resolve_to_an_equity(repo):
    """The trap: ETH is a listed equity in this index.

    Classifying after resolving would track an unrelated company and start showing the
    user its SEC filings. Instrument kind must decide first.
    """
    result = apply_plan(
        repo, plan_import([_stock("ETH", instrument_kind="crypto")], INDEX),
        cap=25, now="t",
    )

    assert _outcomes(result) == {"ETH": "unsupported_instrument"}
    assert repo.list_tracked_ciks() == []


def test_share_classes_collapse_to_one_issuer_regardless_of_order(repo):
    for order in (["GOOG", "GOOGL"], ["GOOGL", "GOOG"]):
        repo.conn.execute("DELETE FROM user_companies")
        repo.conn.execute("DELETE FROM companies")
        repo.conn.commit()

        plan = plan_import([_stock(s) for s in order], INDEX)
        assert len(plan.candidates) == 1        # one issuer, not two watchlist rows
        result = apply_plan(repo, plan, cap=25, now="t")

        # Both symbols are reported, both under the same canonical label.
        assert _outcomes(result) == {"GOOG": "tracked", "GOOGL": "tracked"}
        assert {row.ticker for row in result.rows} == {"GOOG"}
        assert repo.list_tracked_ciks() == [ALPHABET]


def test_reaching_the_cap_fills_the_remaining_slots_and_reports_the_rest(repo):
    for index in range(24):
        cik = f"{index + 1:010d}"
        repo.upsert_company(Company(cik=cik, ticker=f"T{index}", added_at="t"))
        repo.track_company(cik, at="t")

    result = apply_plan(
        repo, plan_import([_stock("AAPL"), _stock("MSFT")], INDEX), cap=25, now="t"
    )

    # One slot left: it is used, and the remainder is named rather than dropped or 409'd.
    assert result.tracked_count == 1
    assert result.cap_reached is True
    assert sorted(_outcomes(result).values()) == ["skipped_cap", "tracked"]
    assert repo.count_tracked_companies() == 25


def test_re_importing_the_same_account_is_idempotent(repo):
    positions = [_stock("AAPL"), _stock("MSFT")]
    first = apply_plan(repo, plan_import(positions, INDEX), cap=25, now="t")
    second = apply_plan(repo, plan_import(positions, INDEX), cap=25, now="t")

    assert set(_outcomes(first).values()) == {"tracked"}
    assert set(_outcomes(second).values()) == {"already_tracked"}
    assert second.tracked_count == 0
    assert repo.count_tracked_companies() == 2


def test_a_stale_ticker_row_fails_only_its_own_symbol(repo):
    """A recycled symbol must not take the rest of the import down with it."""
    repo.upsert_company(Company(cik="0000999999", ticker="MSFT", added_at="t"))

    result = apply_plan(
        repo, plan_import([_stock("AAPL"), _stock("MSFT")], INDEX), cap=25, now="t"
    )

    assert _outcomes(result) == {
        "AAPL": "tracked",
        "MSFT": "ticker_identity_conflict",
    }
    assert repo.list_tracked_ciks() == ["0000320193"]


def test_planning_touches_no_database(repo):
    """plan_import is pure, so a caller can show it for confirmation before writing."""
    plan = plan_import([_stock("AAPL"), _stock("SPY", instrument_kind="etf")], INDEX)

    assert [c.ticker for c in plan.candidates] == ["AAPL"]
    assert [r.outcome for r in plan.rejected] == ["unsupported_instrument"]
    assert repo.list_tracked_ciks() == []
