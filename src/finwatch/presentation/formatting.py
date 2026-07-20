"""Canonical display formatting for deterministic metric results."""

from __future__ import annotations

from finwatch.metrics.envelope import MetricResult


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    displayed = round(value * 100, 1)
    if displayed == 0:
        return "0.0%"
    return f"{displayed:+.1f}%"


def _usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign, absolute = ("−" if value < 0 else ""), abs(value)
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if absolute >= divisor:
            return f"{sign}${absolute / divisor:.1f}{suffix}"
    return f"{sign}${absolute:.0f}"


def _num(value: float | None, places: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def format_metric_value(result: MetricResult) -> str:
    components = result.components
    metric = result.metric
    if metric == "revenue_growth":
        return (
            f"{_pct(components.get('yoy'))} YoY (TTM revenue {_usd(components.get('ttm_revenue'))})"
        )
    if metric in ("net_income_trend", "cfo_trend"):
        direction = components.get("four_quarter_direction", "?")
        return f"{_pct(components.get('yoy'))} YoY · 4-quarter direction {direction}"
    if metric == "liquidity_basics":
        parts = [
            f"cash {_usd(components.get('cash'))}",
            f"net debt {_usd(components.get('net_debt'))}",
        ]
        if components.get("current_ratio") is not None:
            parts.append(f"current ratio {_num(components['current_ratio'])}")
        return " · ".join(parts)
    if metric == "share_count_change":
        # Prefer the deterministic, rounding-aware verdict: the publication gate
        # (verify/compiler.py) judges direction against the SEC `decimals` rounding
        # slack, and a second heuristic here could print "flat" for a move the gate
        # proved was a decrease — then drop a model finding for asserting exactly what
        # this line displays.
        #
        # It is only a preference, not a replacement. The SEC companyfacts API does not
        # emit `decimals` at all (verified: zero occurrences across every cached issuer
        # payload and every recorded fixture), so deterministic_direction is None for
        # real filings today. Deferring to it exclusively deleted the direction clause
        # for every issuer. Fall back to the displayed value so nothing is lost while
        # the gate has no opinion.
        proven = {
            "up": "share count increased",
            "down": "share count decreased",
            "flat": "share count flat",
        }.get(result.deterministic_direction or "")
        if proven is None:
            material_change = result.value if result.value is not None else 0.0
            proven = (
                "share count decreased"
                if material_change <= -0.0005
                else "share count increased"
                if material_change >= 0.0005
                else "share count flat"
            )
        return f"{_pct(result.value)} YoY ({proven})"
    if metric == "simple_leverage":
        parts: list[str] = []
        if components.get("net_debt_to_ebitda") is not None:
            parts.append(
                "net debt / (operating income + D&A) proxy "
                f"{_num(components['net_debt_to_ebitda'])}×"
            )
        if components.get("interest_coverage") is not None:
            parts.append(f"interest coverage {_num(components['interest_coverage'])}×")
        return " · ".join(parts) or "computed"
    return _num(result.value) if result.value is not None else "computed"
