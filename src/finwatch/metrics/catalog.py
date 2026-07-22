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

STARTER_METRIC_LABELS = {
    "revenue_growth": "Revenue growth",
    "net_income_trend": "Net income trend",
    "cfo_trend": "Operating cash flow",
    "liquidity_basics": "Liquidity",
    "share_count_change": "Share count Δ",
    "simple_leverage": "Net debt / (operating income + D&A) proxy",
}

STARTER_METRIC_EXPRESSIONS: dict[str, str] = {
    "revenue_growth": (
        "(current annual revenue − prior annual revenue) ÷ |prior annual revenue|; "
        "TTM revenue is the sum of four contiguous quarters when available"
    ),
    "net_income_trend": (
        "(current annual net income − prior annual net income) ÷ |prior annual net "
        "income|; direction uses four contiguous quarters when available"
    ),
    "cfo_trend": (
        "(current annual operating cash flow − prior annual operating cash flow) ÷ "
        "|prior annual operating cash flow|; direction uses four contiguous quarters "
        "when available"
    ),
    "liquidity_basics": (
        "total debt = long-term debt + short-term debt; net debt = total debt − cash; "
        "current ratio = current assets ÷ current liabilities when available"
    ),
    "share_count_change": (
        "(current shares outstanding − prior shares outstanding) ÷ prior shares "
        "outstanding"
    ),
    "simple_leverage": (
        "net debt = long-term debt + short-term debt − cash; leverage proxy = net debt "
        "÷ (operating income + D&A); interest coverage = operating income ÷ interest "
        "expense when available"
    ),
}
