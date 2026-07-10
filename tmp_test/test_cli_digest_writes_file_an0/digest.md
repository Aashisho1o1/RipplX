# finwatch digest

- **Period covered:** 2024-08-01 → now
- **Holdings tracked:** DPLS, MSFT  ·  watching: AAPL, TWKS
- **Filings in window:** 5

## Critical red flags

### TWKS — 8-K filed 2024-08-02 · CRITICAL · watch — company-level read, no signal
- Non-reliance on prior financials; restatement _(non_reliance)_
- **item_4_02_non_reliance** (critical) — [EDGAR](https://www.sec.gov/Archives/edgar/data/1866550/000186655024000006/twks-20240206.htm): “should no longer be relied upon due to an error in revenue recognition”

### DPLS — 10-K filed 2024-08-02 · CRITICAL · critical_review
- Going concern doubt _(going_concern)_
- Material weakness in controls _(material_weakness)_
- **going_concern** (critical) — [EDGAR](https://www.sec.gov/Archives/edgar/data/866439/000168316824004848/darkpulse_i10k-123123.htm): “raise substantial doubt about its ability to continue as a going concern”
- **material_weakness_with_restatement_risk** (high) — [EDGAR](https://www.sec.gov/Archives/edgar/data/866439/000168316824004848/darkpulse_i10k-123123.htm): “material weakness in internal control over financial reporting”

## What changed

### TWKS (direct) — via TWKS 8-K 2024-08-02
Management has stated that previously issued financial statements should no longer be relied upon, so the historical figures underpinning any view of this company are in question until it restates. As a watch-only name this is a monitoring flag, not a position decision.
- **Channels:** margins (unclear, qualitative), governance (negative, moderate)
- **Guidance:** none_stated · **Liquidity:** unclear · **Net:** negative

### DPLS (direct) — via DPLS 10-K 2024-08-02
The auditor's going-concern paragraph and the disclosed material weakness go to the core of the turnaround thesis: together they question whether the company can keep operating and whether its own reported figures can be relied upon. This is a load-bearing contradiction of the reason to own the stock, not a soft quarter.
- **Channels:** revenue (negative, major), capital structure (negative, major), cash/working capital (negative, major), governance (negative, moderate)
- **Guidance:** none_stated · **Liquidity:** deteriorating · **Net:** negative

## Thesis impact

- **DPLS:** thesis broken

## Verified numbers

_Computed by versioned deterministic formulas from SEC XBRL facts (never by the LLM) and traceable to those facts. ✓ = a computed value; — = not applicable or data missing._

- **DPLS:** no verified financials yet (XBRL facts insufficient or not yet ingested).

### MSFT
| Metric | Value | Formula | ✓ |
|---|---|---|---|
| Revenue growth | +17.5% YoY (TTM revenue $205.1B) | `revenue_growth.v1` | ✓ |
| Net income trend | +38.4% YoY · 4-quarter direction mixed | `net_income_trend.v1` | ✓ |
| Operating cash flow | +26.5% YoY · 4-quarter direction mixed | `cfo_trend.v1` | ✓ |
| Liquidity | cash $19.6B · net debt $25.3B · current ratio 1.24 | `liquidity_basics.v1` | ✓ |
| Share count Δ | +0.0% YoY (dilution) | `share_count_change.v1` | ✓ |
| Leverage | net debt/EBITDA 0.32× · interest coverage 29.80× | `simple_leverage.v1` | ✓ |

## Open questions

- MSFT: rule M6 not evaluated — valuation percentiles computed=0, need 2
- MSFT: rule M7 not evaluated — thesis_verdict=not_assessable
- MSFT: rule M5 not evaluated — weights unavailable

## Boring filings

3 routine filing(s) with no material findings (AAPL 8-K, MSFT 10-Q, AAPL 10-Q).

---

_Educational analysis of public information for the portfolio owner's own decision-making. Not individualized investment advice. Data may be incomplete or delayed._
