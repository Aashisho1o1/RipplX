from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from finwatch.core.types import MetricStatus, sector_from_sic
from finwatch.ingest.service import companyfacts_to_rows
from finwatch.metrics.envelope import MetricResult
from finwatch.metrics.formulas import (
    ANNUAL_FRESHNESS_DAYS,
    RECENT_FRESHNESS_DAYS,
    liquidity_basics,
    revenue_growth,
    share_count_change,
    simple_leverage,
)
from finwatch.xbrl.normalize import Fact, FactStore

AS_OF = "2025-05-01"
GENERAL = sector_from_sic("7372")


def _cf_revenues(entries: list[dict]) -> dict:
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


def test_companyfacts_nonfinite_or_nonnumeric_value_skips_only_that_entry():
    # One corrupt value must not abort the whole issuer's parse (nor be persisted).
    cf = _cf_revenues([
        {"val": 100.0, "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01"},
        {"val": float("nan"), "start": "2022-01-01", "end": "2022-12-31", "filed": "2023-02-01"},
        {"val": float("inf"), "start": "2021-01-01", "end": "2021-12-31", "filed": "2022-02-01"},
        {"val": "N/A", "start": "2020-01-01", "end": "2020-12-31", "filed": "2021-02-01"},
        {"val": True, "start": "2019-01-01", "end": "2019-12-31", "filed": "2020-02-01"},
        {"val": 90.0, "start": "2018-01-01", "end": "2018-12-31", "filed": "2019-02-01"},
    ])

    store = FactStore.from_companyfacts(cf)  # must NOT raise
    kept = sorted(r.fact.value for r in store.annual("revenue", n=10))
    # parity: the DB row builder keeps exactly the same clean values.
    rows = companyfacts_to_rows(cf, "0000000001")

    assert kept == [90.0, 100.0]
    assert sorted(r.value for r in rows) == [90.0, 100.0]


def test_bad_newest_revenue_is_unavailable_not_stale_growth():
    # FY2024 (the genuinely-current annual) has a corrupt value; older years are clean.
    # Skipping the bad newest and pairing FY2023 vs FY2022 would render a plausible but
    # STALE growth number as if current. The poison guard must fail closed instead.
    cf = _cf_revenues([
        {"val": float("nan"), "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01"},
        {"val": 120.0, "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01"},
        {"val": 100.0, "start": "2022-01-01", "end": "2022-12-31", "filed": "2023-02-01"},
    ])
    store = FactStore.from_companyfacts(cf)

    # as_of within the 550-day annual window of the (stale) FY2023 leg, so only the
    # poison guard — not the freshness gate — can prevent a computed growth number.
    result = revenue_growth(store, GENERAL, "2025-03-01")

    assert result.status == MetricStatus.UNAVAILABLE
    assert result.value is None


def test_clean_newest_revenue_still_computes_growth():
    # Control for the poison guard: with a clean FY2024, revenue_growth computes normally.
    cf = _cf_revenues([
        {"val": 132.0, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01"},
        {"val": 120.0, "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01"},
    ])
    result = revenue_growth(FactStore.from_companyfacts(cf), GENERAL, "2025-03-01")

    assert result.status == MetricStatus.COMPUTED


def _instant(tag: str, value: float, end: str = "2024-12-31") -> Fact:
    return Fact(taxonomy="us-gaap", tag=tag, unit="USD", value=value, end=end)


def _duration(tag: str, value: float, start: str, end: str) -> Fact:
    return Fact(
        taxonomy="us-gaap",
        tag=tag,
        unit="USD",
        value=value,
        start=start,
        end=end,
    )


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_xbrl_boundary_rejects_non_finite_values(value):
    with pytest.raises(ValidationError):
        _instant("CashAndCashEquivalentsAtCarryingValue", value)


def test_metric_envelope_rejects_non_finite_computed_components():
    with pytest.raises(ValidationError, match="finite"):
        MetricResult(
            metric="revenue_growth",
            status="computed",
            components={"yoy": float("inf")},
            formula_version="test",
            as_of=AS_OF,
        )


def test_missing_debt_is_unavailable_not_silently_zero():
    store = FactStore([_instant("CashAndCashEquivalentsAtCarryingValue", 100.0)])

    result = liquidity_basics(store, GENERAL, AS_OF)

    assert result.status == MetricStatus.UNAVAILABLE
    assert "long-term debt" in result.unavailable_missing
    assert "short-term debt" in result.unavailable_missing
    assert "net_debt" not in result.components


def test_missing_depreciation_or_debt_cannot_create_leverage_ratio():
    store = FactStore([
        _duration("OperatingIncomeLoss", 50.0, "2024-01-01", "2024-12-31"),
        _instant("CashAndCashEquivalentsAtCarryingValue", 100.0),
    ])

    result = simple_leverage(store, GENERAL, AS_OF)

    assert result.status == MetricStatus.UNAVAILABLE
    assert "depreciation_and_amortization" in result.unavailable_missing
    assert result.value is None


def test_leverage_never_combines_different_annual_duration_starts():
    store = FactStore([
        _duration("OperatingIncomeLoss", 50.0, "2024-01-01", "2024-12-31"),
        _duration(
            "DepreciationDepletionAndAmortization",
            10.0,
            "2024-02-01",
            "2024-12-31",
        ),
        _instant("CashAndCashEquivalentsAtCarryingValue", 100.0),
        _instant("LongTermDebtNoncurrent", 40.0),
        _instant("LongTermDebtCurrent", 10.0),
    ])

    result = simple_leverage(store, GENERAL, AS_OF)

    assert result.status == MetricStatus.UNAVAILABLE
    assert "period or unit alignment" in result.unavailable_missing[0]


def test_noncontiguous_quarters_are_never_summed_as_ttm():
    facts = [
        _duration("Revenues", 500.0, "2024-01-01", "2024-12-31"),
        _duration("Revenues", 400.0, "2023-01-01", "2023-12-31"),
        _duration("Revenues", 130.0, "2024-10-01", "2024-12-31"),
        # Q3 is deliberately absent.
        _duration("Revenues", 110.0, "2024-04-01", "2024-06-30"),
        _duration("Revenues", 100.0, "2024-01-01", "2024-03-31"),
        _duration("Revenues", 90.0, "2023-10-01", "2023-12-31"),
    ]

    result = revenue_growth(FactStore(facts), GENERAL, AS_OF)

    assert result.status == MetricStatus.COMPUTED
    assert result.formula_version == "revenue_growth.v2"
    assert "ttm_revenue" not in result.components


def _annual_revenue_pair(current_end: date) -> FactStore:
    prior_end = current_end - timedelta(days=365)
    return FactStore([
        _duration(
            "Revenues", 120.0,
            (current_end - timedelta(days=365)).isoformat(), current_end.isoformat(),
        ),
        _duration(
            "Revenues", 100.0,
            (prior_end - timedelta(days=365)).isoformat(), prior_end.isoformat(),
        ),
    ])


def test_stale_annual_source_is_unavailable_and_preserves_inputs():
    as_of = date.fromisoformat(AS_OF)
    current_end = as_of - timedelta(days=ANNUAL_FRESHNESS_DAYS + 1)

    result = revenue_growth(_annual_revenue_pair(current_end), GENERAL, AS_OF)

    assert result.status == MetricStatus.UNAVAILABLE
    assert any("current source is stale" in reason for reason in result.unavailable_missing)
    assert any("limit 550 days" in reason for reason in result.unavailable_missing)
    assert [row.value for row in result.inputs_used] == [120.0, 100.0]


def test_annual_freshness_boundary_is_inclusive_and_future_is_rejected():
    as_of = date.fromisoformat(AS_OF)
    boundary = revenue_growth(
        _annual_revenue_pair(as_of - timedelta(days=ANNUAL_FRESHNESS_DAYS)),
        GENERAL,
        AS_OF,
    )
    future = revenue_growth(
        _annual_revenue_pair(as_of + timedelta(days=1)), GENERAL, AS_OF
    )

    assert boundary.status == MetricStatus.COMPUTED
    assert future.status == MetricStatus.UNAVAILABLE
    assert "future-dated" in " ".join(future.unavailable_missing)
    assert len(future.inputs_used) == 2


def _liquidity_at(end: str) -> FactStore:
    return FactStore([
        _instant("CashAndCashEquivalentsAtCarryingValue", 100.0, end),
        _instant("LongTermDebtNoncurrent", 40.0, end),
        _instant("LongTermDebtCurrent", 10.0, end),
    ])


def test_instant_freshness_boundary_and_malformed_dates_fail_closed():
    as_of = date.fromisoformat(AS_OF)
    boundary_end = (as_of - timedelta(days=RECENT_FRESHNESS_DAYS)).isoformat()
    stale_end = (as_of - timedelta(days=RECENT_FRESHNESS_DAYS + 1)).isoformat()

    boundary = liquidity_basics(_liquidity_at(boundary_end), GENERAL, AS_OF)
    stale = liquidity_basics(_liquidity_at(stale_end), GENERAL, AS_OF)
    malformed = liquidity_basics(_liquidity_at("not-a-date"), GENERAL, AS_OF)

    assert boundary.status == MetricStatus.COMPUTED
    assert stale.status == MetricStatus.UNAVAILABLE
    assert len(stale.inputs_used) == 3
    assert "current source is stale" in " ".join(stale.unavailable_missing)
    assert malformed.status == MetricStatus.UNAVAILABLE
    assert "malformed" in " ".join(malformed.unavailable_missing)


def test_share_count_v2_rejects_future_source_and_malformed_as_of():
    as_of = date.fromisoformat(AS_OF)
    current = as_of + timedelta(days=1)
    prior = current - timedelta(days=365)
    store = FactStore([
        _instant("CommonStockSharesOutstanding", 110.0, current.isoformat()),
        _instant("CommonStockSharesOutstanding", 100.0, prior.isoformat()),
    ])

    future = share_count_change(store, GENERAL, AS_OF)
    malformed_as_of = share_count_change(store, GENERAL, "not-a-date")

    assert future.formula_version == "share_count_change.v2"
    assert future.status == MetricStatus.UNAVAILABLE
    assert "future-dated" in " ".join(future.unavailable_missing)
    assert len(future.inputs_used) == 2
    assert malformed_as_of.status == MetricStatus.UNAVAILABLE
    assert "metric as_of date is malformed" in " ".join(
        malformed_as_of.unavailable_missing
    )


def test_malformed_annual_period_never_raises_or_computes():
    store = FactStore([
        _duration("Revenues", 120.0, "bad-start", "bad-end"),
        _duration("Revenues", 100.0, "also-bad", "still-bad"),
    ])

    result = revenue_growth(store, GENERAL, AS_OF)

    assert result.status == MetricStatus.UNAVAILABLE
    assert "annual revenue period dates are malformed" in " ".join(
        result.unavailable_missing
    )
