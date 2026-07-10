"""Launch metric catalog shared by computation and presentation."""

STARTER_METRICS = (
    "revenue_growth",
    "net_income_trend",
    "cfo_trend",
    "liquidity_basics",
    "share_count_change",
    "simple_leverage",
)

STARTER_METRIC_LABELS = {
    "revenue_growth": "Revenue growth",
    "net_income_trend": "Net income trend",
    "cfo_trend": "Operating cash flow",
    "liquidity_basics": "Liquidity",
    "share_count_change": "Share count Δ",
    "simple_leverage": "Net debt / (operating income + D&A) proxy",
}
