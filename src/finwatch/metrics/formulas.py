"""Sector-aware metric formulas. Trust-critical (test-guarded): edit with care, keep the spec tests green.

Every function returns a MetricResult (envelope.py). Rules:
  * Never raise on missing data — return status=unavailable with the missing list.
  * Sector inapplicability -> status=not_applicable with a reason.
  * All inputs recorded in inputs_used; all formulas versioned.
`compute_all` is the single entry point the pipeline may call.
"""
from __future__ import annotations

import math
from typing import Optional, Protocol, Sequence

from pydantic import BaseModel

from finwatch.core.types import MetricStatus, SectorInfo, is_manufacturer_sic
from finwatch.metrics.envelope import InputUsed, MetricResult, MetricsBundle
from finwatch.xbrl.normalize import FactStore, ResolvedFact


class PriceProvider(Protocol):
    def close_on_or_before(self, ticker: str, date_iso: str) -> Optional[float]: ...


class Holding(BaseModel):
    ticker: str
    owned: bool = True
    shares: Optional[float] = None
    cost_basis: Optional[float] = None
    current_weight_pct: Optional[float] = None
    target_weight_pct: Optional[float] = None
    thesis: Optional[str] = None
    horizon: Optional[str] = None


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


def _need(pairs: dict[str, Optional[ResolvedFact]]):
    """Return (missing_names, present_facts)."""
    missing = [k for k, v in pairs.items() if v is None]
    present = [v for v in pairs.values() if v is not None]
    return missing, present


def _direction(values_newest_first: Sequence[float]) -> str:
    v = list(values_newest_first)
    if len(v) < 3:
        return "insufficient_points"
    chron = list(reversed(v))
    ups = all(b > a for a, b in zip(chron, chron[1:]))
    downs = all(b < a for a, b in zip(chron, chron[1:]))
    return "up" if ups else "down" if downs else "mixed"


# ---------------------------------------------------------------- metrics --
def revenue_growth(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "revenue_growth.v1"
    pair = store.yoy_pair("revenue")
    if pair is None:
        return _unavailable("revenue_growth", V, as_of, ["revenue (2 annual periods)"])
    cur, prior = pair
    if prior.fact.value == 0:
        return _unavailable("revenue_growth", V, as_of, ["prior revenue is zero"],
                            inputs=(cur, prior))
    yoy = (cur.fact.value - prior.fact.value) / abs(prior.fact.value)
    q = store.quarterly("revenue", 4)
    ttm = sum(r.fact.value for r in q) if len(q) == 4 else None
    comps = {"yoy": round(yoy, 6)}
    if ttm is not None:
        comps["ttm_revenue"] = ttm
    return _res("revenue_growth", V, as_of, status=MetricStatus.COMPUTED,
                value=round(yoy, 6), components=comps,
                inputs_used=_collect(cur, prior, *q),
                sector_applicability=["universal"])


def _trend_metric(name: str, concept: str, store: FactStore, as_of: str) -> MetricResult:
    V = f"{name}.v1"
    pair = store.yoy_pair(concept)
    if pair is None:
        return _unavailable(name, V, as_of, [f"{concept} (2 annual periods)"])
    cur, prior = pair
    denom = abs(prior.fact.value)
    yoy = None if denom == 0 else (cur.fact.value - prior.fact.value) / denom
    qs = store.quarterly(concept, 4)
    comps = {"current": cur.fact.value, "prior": prior.fact.value,
             "four_quarter_direction": _direction([r.fact.value for r in qs])}
    if yoy is not None:
        comps["yoy"] = round(yoy, 6)
    return _res(name, V, as_of, status=MetricStatus.COMPUTED,
                value=None if yoy is None else round(yoy, 6), components=comps,
                inputs_used=_collect(cur, prior, *qs),
                sector_applicability=["universal"])


def net_income_trend(store, sector, as_of):  # noqa: D103
    return _trend_metric("net_income_trend", "net_income", store, as_of)


def cfo_trend(store, sector, as_of):  # noqa: D103
    return _trend_metric("cfo_trend", "cfo", store, as_of)


def liquidity_basics(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "liquidity_basics.v1"
    cash = store.latest_instant("cash")
    lt = store.latest_instant("lt_debt")
    st = store.latest_instant("st_debt")
    if cash is None:
        return _unavailable("liquidity_basics", V, as_of, ["cash"])
    total_debt = (_val(lt) or 0.0) + (_val(st) or 0.0)
    comps = {"cash": cash.fact.value, "total_debt": total_debt,
             "net_debt": total_debt - cash.fact.value}
    inputs = _collect(cash, lt, st)
    if not sector.is_financial:
        ca, cl = store.latest_instant("current_assets"), store.latest_instant("current_liabilities")
        if ca is not None and cl is not None and cl.fact.value != 0:
            comps["current_ratio"] = round(ca.fact.value / cl.fact.value, 4)
            inputs += _collect(ca, cl)
        else:
            comps["current_ratio"] = None
    else:
        comps["current_ratio_note"] = "not_applicable_financial_institution"
    return _res("liquidity_basics", V, as_of, status=MetricStatus.COMPUTED,
                components=comps, inputs_used=inputs,
                sector_applicability=["universal (current_ratio excluded for financials)"])


def share_count_change(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "share_count_change.v1"
    pair = store.instant_pair("shares_outstanding")
    if pair is None:
        ann = store.yoy_pair("shares_outstanding")
        if ann is None:
            return _unavailable("share_count_change", V, as_of,
                                ["shares_outstanding (2 comparable points)"])
        pair = ann
    cur, prior = pair
    if prior.fact.value == 0:
        return _unavailable("share_count_change", V, as_of, ["prior share count is zero"])
    chg = (cur.fact.value - prior.fact.value) / prior.fact.value
    return _res("share_count_change", V, as_of, status=MetricStatus.COMPUTED,
                value=round(chg, 6),
                components={"current": cur.fact.value, "prior": prior.fact.value},
                inputs_used=_collect(cur, prior), sector_applicability=["universal"])


def simple_leverage(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "simple_leverage.v1"
    APPL = ["general", "manufacturer", "utility"]
    if sector.is_financial:
        return _na("simple_leverage", V, as_of, "financial_institution", APPL)
    op = store.latest_annual("operating_income")
    da = store.latest_annual("dep_amort")
    cash = store.latest_instant("cash")
    lt, st = store.latest_instant("lt_debt"), store.latest_instant("st_debt")
    ie = store.latest_annual("interest_expense")
    missing, _ = _need({"operating_income": op, "cash": cash})
    if missing:
        return _unavailable("simple_leverage", V, as_of, missing)
    ebitda_proxy = op.fact.value + (_val(da) or 0.0)
    net_debt = (_val(lt) or 0.0) + (_val(st) or 0.0) - cash.fact.value
    comps: dict = {"ebitda_proxy": ebitda_proxy, "net_debt": net_debt}
    if ebitda_proxy > 0:
        comps["net_debt_to_ebitda"] = round(net_debt / ebitda_proxy, 4)
    if ie is not None and ie.fact.value not in (0, None):
        comps["interest_coverage"] = round(op.fact.value / ie.fact.value, 4)
    return _res("simple_leverage", V, as_of, status=MetricStatus.COMPUTED,
                value=comps.get("net_debt_to_ebitda"), components=comps,
                inputs_used=_collect(op, da, cash, lt, st, ie),
                sector_applicability=APPL,
                confidence="medium")  # EBITDA proxy, not reported EBITDA


def piotroski_f(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    """9 binary signals; financials skip current-ratio and gross-margin
    components per spec, and the score is scaled to a 9-point equivalent
    (components['score_scaled_9']) for matrix thresholds."""
    V = "piotroski_f.v1"
    need = {
        "net_income": store.yoy_pair("net_income"),
        "total_assets": store.instant_pair("total_assets"),
        "cfo": store.yoy_pair("cfo"),
        "revenue": store.yoy_pair("revenue"),
        "lt_debt": store.instant_pair("lt_debt"),
        "shares": store.instant_pair("shares_outstanding"),
    }
    missing = [k for k, v in need.items() if v is None and k in
               ("net_income", "total_assets", "cfo", "revenue")]
    if missing:
        return _unavailable("piotroski_f", V, as_of,
                            [f"{m} (current+prior)" for m in missing])
    (ni_c, ni_p) = need["net_income"]; (ta_c, ta_p) = need["total_assets"]
    (cf_c, cf_p) = need["cfo"]; (rv_c, rv_p) = need["revenue"]
    inputs = _collect(ni_c, ni_p, ta_c, ta_p, cf_c, cf_p, rv_c, rv_p)

    roa_c = ni_c.fact.value / ta_c.fact.value if ta_c.fact.value else None
    roa_p = ni_p.fact.value / ta_p.fact.value if ta_p.fact.value else None
    comps: dict = {}
    evaluated = 0
    score = 0

    def sig(name: str, cond: Optional[bool]) -> None:
        nonlocal evaluated, score
        if cond is None:
            comps[name] = "skipped"
            return
        evaluated += 1
        comps[name] = bool(cond)
        score += int(cond)

    sig("f1_roa_positive", None if roa_c is None else roa_c > 0)
    sig("f2_cfo_positive", cf_c.fact.value > 0)
    sig("f3_delta_roa_positive",
        None if roa_c is None or roa_p is None else roa_c > roa_p)
    sig("f4_accruals_cfo_gt_ni", cf_c.fact.value > ni_c.fact.value)

    if need["lt_debt"] is not None:
        (ld_c, ld_p) = need["lt_debt"]
        inputs += _collect(ld_c, ld_p)
        lev_c = ld_c.fact.value / ta_c.fact.value if ta_c.fact.value else None
        lev_p = ld_p.fact.value / ta_p.fact.value if ta_p.fact.value else None
        sig("f5_leverage_decreased",
            None if lev_c is None or lev_p is None else lev_c < lev_p)
    else:
        sig("f5_leverage_decreased", True)  # no LT debt reported ≈ no leverage increase
        comps["f5_note"] = "no_lt_debt_reported_treated_as_pass"

    if sector.is_financial:
        comps["f6_current_ratio_improved"] = "skipped_financial"
        comps["f8_gross_margin_improved"] = "skipped_financial"
    else:
        ca, cl = store.instant_pair("current_assets"), store.instant_pair("current_liabilities")
        if ca and cl and cl[0].fact.value and cl[1].fact.value:
            inputs += _collect(*ca, *cl)
            sig("f6_current_ratio_improved",
                (ca[0].fact.value / cl[0].fact.value)
                > (ca[1].fact.value / cl[1].fact.value))
        else:
            sig("f6_current_ratio_improved", None)
        gp = store.yoy_pair("gross_profit")
        if gp and rv_c.fact.value and rv_p.fact.value:
            inputs += _collect(*gp)
            sig("f8_gross_margin_improved",
                (gp[0].fact.value / rv_c.fact.value)
                > (gp[1].fact.value / rv_p.fact.value))
        else:
            sig("f8_gross_margin_improved", None)

    if need["shares"] is not None:
        (sh_c, sh_p) = need["shares"]
        inputs += _collect(sh_c, sh_p)
        sig("f7_no_new_shares", sh_c.fact.value <= sh_p.fact.value * 1.01)
    else:
        sig("f7_no_new_shares", None)

    if all(x.fact.value for x in (ta_c, ta_p)):
        sig("f9_asset_turnover_improved",
            (rv_c.fact.value / ta_c.fact.value)
            > (rv_p.fact.value / ta_p.fact.value))
    else:
        sig("f9_asset_turnover_improved", None)

    if evaluated == 0:
        return _unavailable("piotroski_f", V, as_of, ["no components evaluable"])
    comps["components_evaluated"] = evaluated
    comps["score_scaled_9"] = round(score * 9 / evaluated)
    return _res("piotroski_f", V, as_of, status=MetricStatus.COMPUTED,
                value=float(score), components=comps, inputs_used=inputs,
                sector_applicability=["universal (reduced set for financials)"],
                confidence="high" if evaluated >= 8 else "medium")


def altman_z(store: FactStore, sector: SectorInfo, as_of: str, *,
             ticker: str, price_provider: Optional[PriceProvider]) -> MetricResult:
    """Original Z for manufacturers with a price; Z'' (book equity) otherwise.
    not_applicable for financial institutions."""
    V = "altman_z.v1"
    APPL = ["manufacturer", "general", "utility"]
    if sector.is_financial:
        return _na("altman_z", V, as_of, "financial_institution", APPL)
    ca, cl = store.latest_instant("current_assets"), store.latest_instant("current_liabilities")
    ta, tl = store.latest_instant("total_assets"), store.latest_instant("total_liabilities")
    re_, ebit = store.latest_instant("retained_earnings"), store.latest_annual("operating_income")
    sales, eq = store.latest_annual("revenue"), store.latest_instant("equity")
    sh = store.latest_instant("shares_outstanding")
    missing, _ = _need({"current_assets": ca, "current_liabilities": cl,
                        "total_assets": ta, "total_liabilities": tl,
                        "retained_earnings": re_, "operating_income": ebit})
    if missing or not ta.fact.value or not tl.fact.value:
        return _unavailable("altman_z", V, as_of, missing or ["total_assets/liabilities zero"])
    wc_ta = (ca.fact.value - cl.fact.value) / ta.fact.value
    re_ta = re_.fact.value / ta.fact.value
    ebit_ta = ebit.fact.value / ta.fact.value
    inputs = _collect(ca, cl, ta, tl, re_, ebit, sales, eq, sh)

    price = (price_provider.close_on_or_before(ticker, as_of)
             if price_provider else None)
    use_original = (is_manufacturer_sic(sector.sic) and price is not None
                    and sh is not None and sales is not None)
    if use_original:
        mve = price * sh.fact.value
        z = (1.2 * wc_ta + 1.4 * re_ta + 3.3 * ebit_ta
             + 0.6 * (mve / tl.fact.value)
             + 1.0 * (sales.fact.value / ta.fact.value))
        zone = "distress" if z < 1.81 else "safe" if z > 2.99 else "grey"
        variant, extra = "Z", {"mve": mve, "price_used": price}
    else:
        if eq is None:
            return _unavailable("altman_z", V, as_of, ["equity (for Z'' variant)"])
        z = (6.56 * wc_ta + 3.26 * re_ta + 6.72 * ebit_ta
             + 1.05 * (eq.fact.value / tl.fact.value))
        zone = "distress" if z < 1.1 else "safe" if z > 2.6 else "grey"
        variant, extra = "Z_double_prime", {}
    comps = {"variant": variant, "wc_ta": round(wc_ta, 4), "re_ta": round(re_ta, 4),
             "ebit_ta": round(ebit_ta, 4), **extra}
    return _res("altman_z", V, as_of, status=MetricStatus.COMPUTED,
                value=round(z, 4), zone_or_flag=zone, components=comps,
                inputs_used=inputs, sector_applicability=APPL)


def beneish_m(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    """8-ratio Beneish M-score. ALWAYS confidence=low; corroborating flag only."""
    V = "beneish_m.v1"
    APPL = ["general", "manufacturer"]
    if sector.is_financial:
        return _na("beneish_m", V, as_of, "financial_institution", APPL)
    concepts = ["receivables", "revenue", "gross_profit", "current_assets",
                "ppe_net", "total_assets", "dep_amort", "sga", "lt_debt",
                "current_liabilities", "net_income", "cfo"]
    pairs, missing, inputs = {}, [], []
    for c in concepts:
        p = (store.instant_pair(c) if c in
             ("receivables", "current_assets", "ppe_net", "total_assets",
              "lt_debt", "current_liabilities")
             else store.yoy_pair(c))
        if p is None:
            missing.append(f"{c} (2 fiscal years)")
        else:
            pairs[c] = p
            inputs += _collect(*p)
    if missing:
        return _unavailable("beneish_m", V, as_of, missing)

    def cur(c): return pairs[c][0].fact.value
    def pri(c): return pairs[c][1].fact.value
    try:
        dsri = (cur("receivables") / cur("revenue")) / (pri("receivables") / pri("revenue"))
        gm_c = cur("gross_profit") / cur("revenue"); gm_p = pri("gross_profit") / pri("revenue")
        gmi = gm_p / gm_c
        aq = lambda t, ca, ppe: 1 - (ca + ppe) / t
        aqi = (aq(cur("total_assets"), cur("current_assets"), cur("ppe_net"))
               / aq(pri("total_assets"), pri("current_assets"), pri("ppe_net")))
        sgi = cur("revenue") / pri("revenue")
        dep_rate = lambda d, ppe: d / (d + ppe)
        depi = dep_rate(pri("dep_amort"), pri("ppe_net")) / dep_rate(cur("dep_amort"), cur("ppe_net"))
        sgai = (cur("sga") / cur("revenue")) / (pri("sga") / pri("revenue"))
        lvgi = ((cur("lt_debt") + cur("current_liabilities")) / cur("total_assets")) / \
               ((pri("lt_debt") + pri("current_liabilities")) / pri("total_assets"))
        tata = (cur("net_income") - cur("cfo")) / cur("total_assets")
    except ZeroDivisionError:
        return _unavailable("beneish_m", V, as_of, ["zero denominator in ratio inputs"])
    m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    flag = "elevated_manipulation_risk" if m > -1.78 else "not_flagged"
    comps = {k: round(v, 4) for k, v in dict(DSRI=dsri, GMI=gmi, AQI=aqi, SGI=sgi,
             DEPI=depi, SGAI=sgai, LVGI=lvgi, TATA=tata).items()}
    return _res("beneish_m", V, as_of, status=MetricStatus.COMPUTED,
                value=round(m, 4), zone_or_flag=flag, components=comps,
                inputs_used=inputs, sector_applicability=APPL, confidence="low")


def earnings_quality(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "earnings_quality.v1"
    ni, cf = store.yoy_pair("net_income"), store.yoy_pair("cfo")
    if ni is None or cf is None:
        return _unavailable("earnings_quality", V, as_of, ["net_income/cfo pairs"])
    comps: dict = {"cfo_over_ni_current":
                   None if ni[0].fact.value == 0
                   else round(cf[0].fact.value / ni[0].fact.value, 4)}
    inputs = _collect(*ni, *cf)
    ar, rv = store.instant_pair("receivables"), store.yoy_pair("revenue")
    if ar and rv and all(x.fact.value for x in rv):
        dso_c = 365 * ar[0].fact.value / rv[0].fact.value
        dso_p = 365 * ar[1].fact.value / rv[1].fact.value
        comps["dso_current"], comps["dso_prior"] = round(dso_c, 2), round(dso_p, 2)
        comps["dso_rising"] = dso_c > dso_p * 1.10
        inputs += _collect(*ar)
    inv = store.instant_pair("inventory")
    if inv and rv and inv[1].fact.value and rv[1].fact.value:
        inv_g = (inv[0].fact.value - inv[1].fact.value) / inv[1].fact.value
        rev_g = (rv[0].fact.value - rv[1].fact.value) / abs(rv[1].fact.value)
        comps["inventory_growth_gap"] = round(inv_g - rev_g, 4)
        inputs += _collect(*inv)
    elif sector.is_financial:
        comps["inventory_note"] = "not_applicable_financial_institution"
    return _res("earnings_quality", V, as_of, status=MetricStatus.COMPUTED,
                components=comps, inputs_used=inputs,
                sector_applicability=["universal (inventory component conditional)"])


# --------------------------------------------------- price-dependent set --
def _market_cap(store, ticker, as_of, pp) -> Optional[tuple[float, float, ResolvedFact]]:
    sh = store.latest_instant("shares_outstanding")
    if sh is None or pp is None:
        return None
    price = pp.close_on_or_before(ticker, as_of)
    if price is None:
        return None
    return price * sh.fact.value, price, sh


def valuation_percentile(store: FactStore, sector: SectorInfo, as_of: str, *,
                         ticker: str, price_provider: Optional[PriceProvider],
                         multiple: str) -> MetricResult:
    """Percentile of the current multiple vs the issuer's own fiscal-year-end
    history (needs >=3 historical points). multiple in {'pe','ev_ebitda','p_fcf'}."""
    V = f"valuation_percentile.{multiple}.v1"
    name = f"valuation_pct_{multiple}"
    if sector.is_financial and multiple in ("ev_ebitda", "p_fcf"):
        return _na(name, V, as_of, "financial_institution", ["general"])
    mc = _market_cap(store, ticker, as_of, price_provider)
    if mc is None:
        return _unavailable(name, V, as_of, ["price or shares_outstanding"])
    mcap, price, sh = mc

    def denom_at(idx: int) -> Optional[float]:
        ann = {
            "pe": store.annual("net_income", 7),
            "ev_ebitda": store.annual("operating_income", 7),
            "p_fcf": store.annual("cfo", 7),
        }[multiple]
        if idx >= len(ann):
            return None
        v = ann[idx].fact.value
        if multiple == "ev_ebitda":
            da = store.annual("dep_amort", 7)
            if idx < len(da):
                v += da[idx].fact.value
        if multiple == "p_fcf":
            cx = store.annual("capex", 7)
            if idx < len(cx):
                v -= cx[idx].fact.value
        return v

    ann_dates = [r.fact.end for r in store.annual(
        {"pe": "net_income", "ev_ebitda": "operating_income", "p_fcf": "cfo"}[multiple], 7)]
    lt = store.latest_instant("lt_debt"); st = store.latest_instant("st_debt")
    cash = store.latest_instant("cash")
    net_debt = ((_val(lt) or 0.0) + (_val(st) or 0.0) - (_val(cash) or 0.0))

    # Period-matched capital structure for the HISTORY (F12): a prior year's multiple must
    # use that year's shares (and net debt for EV), not today's. Fall back to current values
    # only when a year's instant is missing, and drop confidence to low when we do.
    sh_at = {r.fact.end: r.fact.value for r in store.instant("shares_outstanding", 12)}
    lt_at = {r.fact.end: r.fact.value for r in store.instant("lt_debt", 12)}
    st_at = {r.fact.end: r.fact.value for r in store.instant("st_debt", 12)}
    cash_at = {r.fact.end: r.fact.value for r in store.instant("cash", 12)}

    def _net_debt_at(d_end: str) -> Optional[float]:
        parts = (lt_at.get(d_end), st_at.get(d_end), cash_at.get(d_end))
        if all(p is None for p in parts):
            return None
        return (parts[0] or 0.0) + (parts[1] or 0.0) - (parts[2] or 0.0)

    def multiple_value(numer_mcap: float, d: Optional[float], nd: float) -> Optional[float]:
        if d is None or d <= 0:
            return None
        n = numer_mcap + nd if multiple == "ev_ebitda" else numer_mcap
        return n / d

    current = multiple_value(mcap, denom_at(0), net_debt)   # index 0 = today (correct)
    if current is None:
        return _unavailable(name, V, as_of, [f"non-positive denominator for {multiple}"])
    history: list[float] = []
    approximated = False
    for i, d_end in enumerate(ann_dates[1:], start=1):
        p = price_provider.close_on_or_before(ticker, d_end)
        if p is None:
            continue
        sh_i, nd_i = sh_at.get(d_end), _net_debt_at(d_end)
        if sh_i is None or (multiple == "ev_ebitda" and nd_i is None):
            sh_i, nd_i, approximated = sh.fact.value, net_debt, True   # current fallback
        mv = multiple_value(p * sh_i, denom_at(i), nd_i if nd_i is not None else net_debt)
        if mv is not None:
            history.append(mv)
    if len(history) < 3:
        return _unavailable(name, V, as_of,
                            [f"insufficient_history ({len(history)} points, need 3)"])
    below = sum(1 for h in history if h < current)
    pct = 100.0 * below / len(history)
    return _res(name, V, as_of, status=MetricStatus.COMPUTED, value=round(pct, 1),
                components={"current_multiple": round(current, 3),
                            "history_points": len(history),
                            "history_median": round(sorted(history)[len(history)//2], 3),
                            "history_capital_structure":
                                "current_fallback" if approximated else "period_matched"},
                inputs_used=_collect(sh, lt, st, cash),
                sector_applicability=["general"],
                confidence="low" if approximated else "medium")


def fcf_yield(store, sector, as_of, *, ticker, price_provider) -> MetricResult:
    V = "fcf_yield.v1"
    if sector.is_financial:
        return _na("fcf_yield", V, as_of, "financial_institution", ["general"])
    mc = _market_cap(store, ticker, as_of, price_provider)
    cf, cx = store.latest_annual("cfo"), store.latest_annual("capex")
    if mc is None or cf is None:
        return _unavailable("fcf_yield", V, as_of,
                            ["market_cap"] if mc is None else ["cfo"])
    mcap = mc[0]
    fcf = cf.fact.value - (_val(cx) or 0.0)
    return _res("fcf_yield", V, as_of, status=MetricStatus.COMPUTED,
                value=round(fcf / mcap, 6),
                components={"fcf": fcf, "market_cap": mcap},
                inputs_used=_collect(cf, cx, mc[2]),
                sector_applicability=["general"])


def peg(store, sector, as_of, *, ticker, price_provider) -> MetricResult:
    V = "peg.v1"
    mc = _market_cap(store, ticker, as_of, price_provider)
    pair = store.yoy_pair("net_income")
    if mc is None or pair is None:
        return _unavailable("peg", V, as_of, ["market_cap or net_income pair"])
    cur, prior = pair
    if cur.fact.value <= 0 or prior.fact.value <= 0:
        return _na("peg", V, as_of, "negative_eps_or_base", ["general"])
    growth_pct = 100.0 * (cur.fact.value - prior.fact.value) / prior.fact.value
    if growth_pct <= 0:
        return _na("peg", V, as_of, "non_positive_growth", ["general"])
    pe = mc[0] / cur.fact.value
    return _res("peg", V, as_of, status=MetricStatus.COMPUTED,
                value=round(pe / growth_pct, 4),
                components={"pe": round(pe, 3), "eps_growth_pct": round(growth_pct, 3)},
                inputs_used=_collect(cur, prior, mc[2]),
                sector_applicability=["general"], confidence="medium")


def graham_number(store, sector, as_of) -> MetricResult:
    V = "graham_number.v1"
    ni, eq = store.latest_annual("net_income"), store.latest_instant("equity")
    sh = store.latest_instant("shares_outstanding")
    missing, _ = _need({"net_income": ni, "equity": eq, "shares_outstanding": sh})
    if missing:
        return _unavailable("graham_number", V, as_of, missing)
    if not sh.fact.value:
        return _unavailable("graham_number", V, as_of, ["shares zero"])
    eps, bvps = ni.fact.value / sh.fact.value, eq.fact.value / sh.fact.value
    if eps <= 0 or bvps <= 0:
        return _na("graham_number", V, as_of, "negative_eps_or_bvps", ["general"])
    return _res("graham_number", V, as_of, status=MetricStatus.COMPUTED,
                value=round(math.sqrt(22.5 * eps * bvps), 4),
                components={"eps": round(eps, 4), "bvps": round(bvps, 4)},
                inputs_used=_collect(ni, eq, sh),
                sector_applicability=["general"], confidence="low")


# ------------------------------------------------------ portfolio metrics --
def position_metrics(holding: Holding, price: Optional[float],
                     portfolio_market_value: Optional[float], as_of: str) -> MetricResult:
    V = "position_metrics.v1"
    if not holding.owned:
        return _na("position_metrics", V, as_of, "watch_only_record", ["owned"])
    if price is None or holding.shares is None:
        return _unavailable("position_metrics", V, as_of, ["price or shares"])
    mv = price * holding.shares
    comps: dict = {"market_value": round(mv, 2)}
    if portfolio_market_value:
        comps["weight_pct"] = round(100.0 * mv / portfolio_market_value, 3)
        if holding.target_weight_pct:
            comps["weight_over_target"] = round(
                comps["weight_pct"] / holding.target_weight_pct, 3)
    if holding.cost_basis:
        comps["unrealized_pl_pct"] = round(
            100.0 * (price - holding.cost_basis) / holding.cost_basis, 3)
    return _res("position_metrics", V, as_of, status=MetricStatus.COMPUTED,
                components=comps, sector_applicability=["owned"])


def rebalance_check(current_weight_pct: Optional[float],
                    target_weight_pct: Optional[float], as_of: str) -> MetricResult:
    """5/25 bands: fires on drift >=5 absolute points OR >=25% relative."""
    V = "rebalance_check.v1"
    if current_weight_pct is None or target_weight_pct in (None, 0):
        return _unavailable("rebalance_check", V, as_of, ["weights"])
    drift = current_weight_pct - target_weight_pct
    rel = abs(drift) / target_weight_pct
    fires = abs(drift) >= 5.0 or rel >= 0.25
    return _res("rebalance_check", V, as_of, status=MetricStatus.COMPUTED,
                value=1.0 if fires else 0.0, zone_or_flag="fires" if fires else "within_bands",
                components={"drift_abs_pts": round(drift, 3), "drift_rel": round(rel, 4)},
                sector_applicability=["owned"])


# ------------------------------------------------------------ entry point --
def compute_all(store: FactStore, sector: SectorInfo, *, ticker: str,
                price_provider: Optional[PriceProvider], as_of: str,
                holding: Optional[Holding] = None,
                portfolio_market_value: Optional[float] = None) -> MetricsBundle:
    """The ONLY metrics entry point the pipeline may call."""
    b = MetricsBundle()
    b.results["revenue_growth"] = revenue_growth(store, sector, as_of)
    b.results["net_income_trend"] = net_income_trend(store, sector, as_of)
    b.results["cfo_trend"] = cfo_trend(store, sector, as_of)
    b.results["liquidity_basics"] = liquidity_basics(store, sector, as_of)
    b.results["share_count_change"] = share_count_change(store, sector, as_of)
    b.results["simple_leverage"] = simple_leverage(store, sector, as_of)
    b.results["piotroski_f"] = piotroski_f(store, sector, as_of)
    b.results["altman_z"] = altman_z(store, sector, as_of, ticker=ticker,
                                     price_provider=price_provider)
    b.results["beneish_m"] = beneish_m(store, sector, as_of)
    b.results["earnings_quality"] = earnings_quality(store, sector, as_of)
    for mult in ("pe", "ev_ebitda", "p_fcf"):
        b.valuations.append(valuation_percentile(
            store, sector, as_of, ticker=ticker,
            price_provider=price_provider, multiple=mult))
    b.results["fcf_yield"] = fcf_yield(store, sector, as_of, ticker=ticker,
                                       price_provider=price_provider)
    b.results["peg"] = peg(store, sector, as_of, ticker=ticker,
                           price_provider=price_provider)
    b.results["graham_number"] = graham_number(store, sector, as_of)
    if holding is not None:
        price = (price_provider.close_on_or_before(ticker, as_of)
                 if price_provider else None)
        b.results["position_metrics"] = position_metrics(
            holding, price, portfolio_market_value, as_of)
        pm = b.results["position_metrics"].components
        b.results["rebalance_check"] = rebalance_check(
            pm.get("weight_pct", holding.current_weight_pct),
            holding.target_weight_pct, as_of)
    return b
