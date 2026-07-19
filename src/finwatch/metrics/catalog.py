"""Launch metric catalog shared by computation, tools, schemas, and presentation."""

from enum import StrEnum


class MetricId(StrEnum):
    REVENUE_GROWTH = "revenue_growth"
    NET_INCOME_TREND = "net_income_trend"
    CFO_TREND = "cfo_trend"
    LIQUIDITY_BASICS = "liquidity_basics"
    SHARE_COUNT_CHANGE = "share_count_change"
    SIMPLE_LEVERAGE = "simple_leverage"

STARTER_METRICS = tuple(metric.value for metric in MetricId)

DIRECTIONAL_METRICS = frozenset({
    MetricId.REVENUE_GROWTH,
    MetricId.NET_INCOME_TREND,
    MetricId.CFO_TREND,
    MetricId.SHARE_COUNT_CHANGE,
})

STARTER_METRIC_LABELS = {
    "revenue_growth": "Revenue growth",
    "net_income_trend": "Net income trend",
    "cfo_trend": "Operating cash flow",
    "liquidity_basics": "Liquidity",
    "share_count_change": "Share count Δ",
    "simple_leverage": "Net debt / (operating income + D&A) proxy",
}
