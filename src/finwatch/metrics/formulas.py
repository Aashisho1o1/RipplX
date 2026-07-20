"""Sector-aware metric formulas. Trust-critical (test-guarded): edit with care, keep the spec tests green.

Every function returns a MetricResult (envelope.py). Rules:
  * Never raise on missing data — return status=unavailable with the missing list.
  * Sector inapplicability -> status=not_applicable with a reason.
  * All inputs recorded in inputs_used; all formulas versioned.
The launch pipeline calls ``compute_starter`` — exactly the six shipped metrics.
"""
from __future__ import annotations

import math
from decimal import Decimal, DecimalException
from datetime import date
from typing import Optional, Sequence

from finwatch.core.types import MetricStatus, SectorInfo
from finwatch.metrics.envelope import InputUsed, MetricResult, MetricsBundle
from finwatch.xbrl.normalize import FactStore, ResolvedFact

ANNUAL_FRESHNESS_DAYS = 550
RECENT_FRESHNESS_DAYS = 200


# ---------------------------------------------------------------- helpers --
def _res(metric: str, version: str, as_of: str, **kw) -> MetricResult:
    return MetricResult(metric=metric, formula_version=version, as_of=as_of, **kw)


def _unavailable(metric, version, as_of, missing, inputs=()):
    return _res(metric, version, as_of, status=MetricStatus.UNAVAILABLE,
                unavailable_missing=list(missing),
                inputs_used=[r.to_input_used() for r in inputs])


def _na(metric, version, as_of, reason, applicability):
    return _res(metric, version, as_of, status=MetricStatus.NOT_APPLICABLE,
                not_applicable_reason=reason, sector_applicability=applicability)


def _val(rf: Optional[ResolvedFact]) -> Optional[float]:
    return None if rf is None else rf.fact.value


def _collect(*rfs: Optional[ResolvedFact]) -> list[InputUsed]:
    return [r.to_input_used() for r in rfs if r is not None]


def xbrl_rounding_slack(decimals: str | None) -> float | None:
    """Maximum absolute rounding error implied by SEC XBRL ``decimals``."""
    if decimals is None:
        return None
    value = decimals.strip().upper()
    if value == "INF":
        return 0.0
    try:
        exponent = int(value)
        exact_slack = Decimal("0.5").scaleb(-exponent)
        slack = float(exact_slack)
    except (DecimalException, TypeError, ValueError, OverflowError):
        return None
    if not exact_slack.is_finite() or not math.isfinite(slack):
        return None
    if exact_slack != 0 and slack == 0.0:
        return None
    return slack


def _finite_float(value: Decimal) -> float | None:
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (value != 0 and result == 0.0):
        return None
    return result


def _direction_fields(current: ResolvedFact, prior: ResolvedFact) -> dict:
    current_slack = xbrl_rounding_slack(current.fact.decimals)
    prior_slack = xbrl_rounding_slack(prior.fact.decimals)
    delta = _finite_float(
        Decimal(str(current.fact.value)) - Decimal(str(prior.fact.value))
    )
    combined_slack = (
        _finite_float(Decimal(str(current_slack)) + Decimal(str(prior_slack)))
        if current_slack is not None and prior_slack is not None
        else None
    )
    return {
        "direction_delta": delta,
        "direction_slack": combined_slack,
        "direction_basis": "current_minus_prior",
    }


def _need(pairs: dict[str, Optional[ResolvedFact]]):
    """Return (missing_names, present_facts)."""
    missing = [k for k, v in pairs.items() if v is None]
    present = [v for v in pairs.values() if v is not None]
    return missing, present


def _parse_as_of(as_of: str) -> tuple[date | None, str | None]:
    try:
        return date.fromisoformat(as_of), None
    except (TypeError, ValueError):
        return None, (
            "current source freshness unavailable: "
            f"metric as_of date is malformed ({as_of!r})"
        )


def _freshness_errors(
    as_of: str,
    facts: Sequence[ResolvedFact],
    *,
    max_age_days: int,
    source_kind: str,
) -> list[str]:
    """Explain why current source facts cannot be treated as current.

    Only the newest/current legs are checked. Prior-year comparison legs are
    intentionally older by definition and remain recorded in ``inputs_used``.
    """
    cutoff, as_of_error = _parse_as_of(as_of)
    if as_of_error:
        return [as_of_error]
    assert cutoff is not None
    errors: list[str] = []
    for row in facts:
        end = row.fact.end
        try:
            period_end = date.fromisoformat(end) if end is not None else None
        except (TypeError, ValueError):
            period_end = None
        label = f"{source_kind} {row.concept}"
        if period_end is None:
            errors.append(
                "current source freshness unavailable: "
                f"{label} period end is missing or malformed ({end!r})"
            )
            continue
        age = (cutoff - period_end).days
        if age < 0:
            errors.append(
                "current source is future-dated: "
                f"{label} period end {end} is after as_of {as_of}"
            )
        elif age > max_age_days:
            errors.append(
                "current source is stale: "
                f"{label} period end {end} is {age} days before as_of {as_of} "
                f"(limit {max_age_days} days)"
            )
    return errors


def _malformed_period_error(source_kind: str) -> str:
    return (
        "current source freshness unavailable: "
        f"{source_kind} period dates are malformed"
    )


def _direction(values_newest_first: Sequence[float]) -> str:
    v = list(values_newest_first)
    if len(v) < 3:
        return "insufficient_points"
    chron = list(reversed(v))
    ups = all(b > a for a, b in zip(chron, chron[1:]))
    downs = all(b < a for a, b in zip(chron, chron[1:]))
    return "up" if ups else "down" if downs else "mixed"


def _same_period_and_unit(*facts: Optional[ResolvedFact]) -> bool:
    present = [row for row in facts if row is not None]
    return bool(present) and len({row.fact.end for row in present}) == 1 \
        and len({row.fact.start for row in present}) == 1 \
        and len({row.fact.unit for row in present}) == 1


def _contiguous_quarters(rows: Sequence[ResolvedFact]) -> bool:
    """Four newest-first fiscal quarters with no missing period between them."""
    if len(rows) != 4 or any(row.fact.end is None for row in rows):
        return False
    ends = [date.fromisoformat(row.fact.end) for row in rows]
    return all(70 <= (newer - older).days <= 120 for newer, older in zip(ends, ends[1:]))


# ---------------------------------------------------------------- metrics --
def revenue_growth(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "revenue_growth.v5"
    _, as_of_error = _parse_as_of(as_of)
    if as_of_error:
        return _unavailable("revenue_growth", V, as_of, [as_of_error])
    try:
        pair = store.yoy_pair("revenue")
    except (TypeError, ValueError):
        return _unavailable(
            "revenue_growth", V, as_of, [_malformed_period_error("annual revenue")]
        )
    if pair is None:
        return _unavailable("revenue_growth", V, as_of, ["revenue (2 annual periods)"])
    cur, prior = pair
    freshness = _freshness_errors(
        as_of, [cur], max_age_days=ANNUAL_FRESHNESS_DAYS, source_kind="annual"
    )
    if freshness:
        return _unavailable("revenue_growth", V, as_of, freshness, inputs=(cur, prior))
    if prior.fact.value == 0:
        return _unavailable("revenue_growth", V, as_of, ["prior revenue is zero"],
                            inputs=(cur, prior))
    yoy = (cur.fact.value - prior.fact.value) / abs(prior.fact.value)
    try:
        q = store.quarterly("revenue", 4)
    except (TypeError, ValueError):
        q = []
    q_freshness = _freshness_errors(
        as_of, q[:1], max_age_days=RECENT_FRESHNESS_DAYS, source_kind="quarterly"
    )
    ttm = (
        sum(r.fact.value for r in q)
        if not q_freshness and _contiguous_quarters(q)
        else None
    )
    comps = {"yoy": round(yoy, 6)}
    if ttm is not None:
        comps["ttm_revenue"] = ttm
    elif q_freshness:
        comps["ttm_source_reason"] = "; ".join(q_freshness)
    return _res("revenue_growth", V, as_of, status=MetricStatus.COMPUTED,
                value=round(yoy, 6), components=comps,
                inputs_used=_collect(cur, prior, *(q if not q_freshness else [])),
                **_direction_fields(cur, prior),
                sector_applicability=["universal"])


def _trend_metric(name: str, concept: str, store: FactStore, as_of: str) -> MetricResult:
    V = f"{name}.v4"
    _, as_of_error = _parse_as_of(as_of)
    if as_of_error:
        return _unavailable(name, V, as_of, [as_of_error])
    try:
        pair = store.yoy_pair(concept)
    except (TypeError, ValueError):
        return _unavailable(
            name, V, as_of, [_malformed_period_error(f"annual {concept}")]
        )
    if pair is None:
        return _unavailable(name, V, as_of, [f"{concept} (2 annual periods)"])
    cur, prior = pair
    freshness = _freshness_errors(
        as_of, [cur], max_age_days=ANNUAL_FRESHNESS_DAYS, source_kind="annual"
    )
    if freshness:
        return _unavailable(name, V, as_of, freshness, inputs=(cur, prior))
    denom = abs(prior.fact.value)
    yoy = None if denom == 0 else (cur.fact.value - prior.fact.value) / denom
    try:
        qs = store.quarterly(concept, 4)
    except (TypeError, ValueError):
        qs = []
    q_freshness = _freshness_errors(
        as_of, qs[:1], max_age_days=RECENT_FRESHNESS_DAYS, source_kind="quarterly"
    )
    direction = (
        _direction([r.fact.value for r in qs])
        if not q_freshness and _contiguous_quarters(qs)
        else "unavailable_stale_source" if q_freshness else "insufficient_points"
    )
    comps = {"current": cur.fact.value, "prior": prior.fact.value,
             "four_quarter_direction": direction}
    if q_freshness:
        comps["four_quarter_source_reason"] = "; ".join(q_freshness)
    if yoy is not None:
        comps["yoy"] = round(yoy, 6)
    return _res(name, V, as_of, status=MetricStatus.COMPUTED,
                value=None if yoy is None else round(yoy, 6), components=comps,
                inputs_used=_collect(cur, prior, *(qs if not q_freshness else [])),
                **_direction_fields(cur, prior),
                sector_applicability=["universal"])


def net_income_trend(store, sector, as_of):  # noqa: D103
    return _trend_metric("net_income_trend", "net_income", store, as_of)


def cfo_trend(store, sector, as_of):  # noqa: D103
    return _trend_metric("cfo_trend", "cfo", store, as_of)


def liquidity_basics(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "liquidity_basics.v2"
    if sector.is_financial:
        return _na("liquidity_basics", V, as_of,
                   "financial_institution_balance_sheet", ["general", "utility"])
    _, as_of_error = _parse_as_of(as_of)
    if as_of_error:
        return _unavailable("liquidity_basics", V, as_of, [as_of_error])
    cash = store.latest_instant("cash")
    lt = store.latest_instant("lt_debt")
    st = store.latest_instant("st_debt")
    missing, present = _need({"cash": cash, "long-term debt": lt, "short-term debt": st})
    if missing:
        return _unavailable("liquidity_basics", V, as_of, missing, present)
    freshness = _freshness_errors(
        as_of, present, max_age_days=RECENT_FRESHNESS_DAYS, source_kind="instant"
    )
    if freshness:
        return _unavailable("liquidity_basics", V, as_of, freshness, present)
    if not _same_period_and_unit(cash, lt, st):
        return _unavailable("liquidity_basics", V, as_of,
                            ["cash/debt period or unit alignment"], present)
    total_debt = lt.fact.value + st.fact.value
    comps = {"cash": cash.fact.value, "total_debt": total_debt,
             "net_debt": total_debt - cash.fact.value}
    inputs = _collect(cash, lt, st)
    ca, cl = store.latest_instant("current_assets"), store.latest_instant("current_liabilities")
    if ca is not None and cl is not None and cl.fact.value != 0:
        current_ratio_freshness = _freshness_errors(
            as_of, [ca, cl], max_age_days=RECENT_FRESHNESS_DAYS, source_kind="instant"
        )
        if not current_ratio_freshness and _same_period_and_unit(cash, ca, cl):
            comps["current_ratio"] = round(ca.fact.value / cl.fact.value, 4)
            inputs += _collect(ca, cl)
        else:
            comps["current_ratio"] = None
            if current_ratio_freshness:
                comps["current_ratio_source_reason"] = "; ".join(
                    current_ratio_freshness
                )
    else:
        comps["current_ratio"] = None
    return _res("liquidity_basics", V, as_of, status=MetricStatus.COMPUTED,
                components=comps, inputs_used=inputs,
                sector_applicability=["universal (current_ratio excluded for financials)"])


def share_count_change(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "share_count_change.v4"
    _, as_of_error = _parse_as_of(as_of)
    if as_of_error:
        return _unavailable("share_count_change", V, as_of, [as_of_error])
    try:
        pair = store.instant_pair("shares_outstanding")
    except (TypeError, ValueError):
        return _unavailable(
            "share_count_change", V, as_of,
            [_malformed_period_error("share count")],
        )
    if pair is None:
        try:
            ann = store.yoy_pair("shares_outstanding")
        except (TypeError, ValueError):
            return _unavailable(
                "share_count_change", V, as_of,
                [_malformed_period_error("share count")],
            )
        if ann is None:
            return _unavailable("share_count_change", V, as_of,
                                ["shares_outstanding (2 comparable points)"])
        pair = ann
    cur, prior = pair
    freshness = _freshness_errors(
        as_of, [cur], max_age_days=RECENT_FRESHNESS_DAYS, source_kind="share count"
    )
    if freshness:
        return _unavailable("share_count_change", V, as_of, freshness, inputs=(cur, prior))
    if prior.fact.value == 0:
        return _unavailable(
            "share_count_change", V, as_of, ["prior share count is zero"],
            inputs=(cur, prior),
        )
    chg = (cur.fact.value - prior.fact.value) / prior.fact.value
    return _res("share_count_change", V, as_of, status=MetricStatus.COMPUTED,
                value=round(chg, 6),
                components={"current": cur.fact.value, "prior": prior.fact.value},
                inputs_used=_collect(cur, prior), **_direction_fields(cur, prior),
                sector_applicability=["universal"])


def simple_leverage(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "simple_leverage.v2"
    APPL = ["general", "manufacturer", "utility"]
    if sector.is_financial:
        return _na("simple_leverage", V, as_of, "financial_institution", APPL)
    _, as_of_error = _parse_as_of(as_of)
    if as_of_error:
        return _unavailable("simple_leverage", V, as_of, [as_of_error])
    try:
        op = store.latest_annual("operating_income")
        da = store.latest_annual("dep_amort")
    except (TypeError, ValueError):
        return _unavailable(
            "simple_leverage", V, as_of,
            [_malformed_period_error("annual operating income/depreciation")],
        )
    cash = store.latest_instant("cash")
    lt, st = store.latest_instant("lt_debt"), store.latest_instant("st_debt")
    try:
        ie = store.latest_annual("interest_expense")
        ie_date_error = None
    except (TypeError, ValueError):
        ie = None
        ie_date_error = _malformed_period_error("annual interest expense")
    missing, present = _need({
        "operating_income": op,
        "depreciation_and_amortization": da,
        "cash": cash,
        "long-term debt": lt,
        "short-term debt": st,
    })
    if missing:
        return _unavailable("simple_leverage", V, as_of, missing, present)
    freshness = [
        *_freshness_errors(
            as_of, [op, da], max_age_days=ANNUAL_FRESHNESS_DAYS,
            source_kind="annual",
        ),
        *_freshness_errors(
            as_of, [cash, lt, st], max_age_days=RECENT_FRESHNESS_DAYS,
            source_kind="instant",
        ),
    ]
    if freshness:
        return _unavailable(
            "simple_leverage", V, as_of, freshness,
            inputs=(*present, *([ie] if ie is not None else [])),
        )
    if not _same_period_and_unit(op, da):
        return _unavailable("simple_leverage", V, as_of,
                            ["operating income/depreciation period or unit alignment"], present)
    if not _same_period_and_unit(cash, lt, st):
        return _unavailable("simple_leverage", V, as_of,
                            ["cash/debt period or unit alignment"], present)
    ebitda_proxy = op.fact.value + da.fact.value
    net_debt = lt.fact.value + st.fact.value - cash.fact.value
    comps: dict = {"ebitda_proxy": ebitda_proxy, "net_debt": net_debt}
    if ebitda_proxy > 0:
        comps["net_debt_to_ebitda"] = round(net_debt / ebitda_proxy, 4)
    ie_freshness = (
        _freshness_errors(
            as_of, [ie], max_age_days=ANNUAL_FRESHNESS_DAYS,
            source_kind="annual",
        )
        if ie is not None
        else []
    )
    usable_ie = ie if not ie_freshness and ie_date_error is None else None
    if (usable_ie is not None and usable_ie.fact.value not in (0, None)
            and _same_period_and_unit(op, usable_ie)):
        comps["interest_coverage"] = round(op.fact.value / usable_ie.fact.value, 4)
    elif ie_freshness or ie_date_error:
        comps["interest_coverage_source_reason"] = "; ".join(
            [*ie_freshness, *([ie_date_error] if ie_date_error else [])]
        )
    return _res("simple_leverage", V, as_of, status=MetricStatus.COMPUTED,
                value=comps.get("net_debt_to_ebitda"), components=comps,
                inputs_used=_collect(op, da, cash, lt, st, usable_ie),
                sector_applicability=APPL,
                confidence="medium")  # EBITDA proxy, not reported EBITDA


# ------------------------------------------------------------ entry point --
def compute_starter(store: FactStore, sector: SectorInfo, *, as_of: str) -> MetricsBundle:
    """Compute exactly the six metrics exposed by the launch product.

    No price, position, valuation, scoring, or portfolio inputs enter this path.
    """
    bundle = MetricsBundle()
    bundle.results["revenue_growth"] = revenue_growth(store, sector, as_of)
    bundle.results["net_income_trend"] = net_income_trend(store, sector, as_of)
    bundle.results["cfo_trend"] = cfo_trend(store, sector, as_of)
    bundle.results["liquidity_basics"] = liquidity_basics(store, sector, as_of)
    bundle.results["share_count_change"] = share_count_change(store, sector, as_of)
    bundle.results["simple_leverage"] = simple_leverage(store, sector, as_of)
    return bundle
