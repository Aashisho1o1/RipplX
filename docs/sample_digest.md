# finwatch digest — launch sample

> One holding needs a critical review.

- **Period covered:** 2024-01-01 → now
- **Holdings tracked:** DPLS, MSFT · watching: AAPL, TWKS
- **Filings in window:** 5 · **Analyzed:** 5

## AI-selected changes (evidence verified)

_The model selects and summarizes importance. Deterministic checks prove that each displayed
quotation is exact; they do not prove the model's interpretation._

### DPLS — 10-K filed 2024-07-15

- **Going concern doubt** _(AI: CRITICAL)_
  - Evidence: “raise substantial doubt about its ability to continue as a going concern”
- **Material weakness in controls** _(AI: HIGH)_
  - Evidence: “material weakness in internal control over financial reporting”

### MSFT — 10-Q filed 2024-04-25

- **Quarterly revenue increased** _(AI: MEDIUM)_
  - Evidence: “Revenue increased $2.1 billion or 12%”

Every production evidence row links to its canonical HTTPS SEC filing and carries accession,
section-relative offsets, and the verified section hash. Links are omitted from this compact
documentation excerpt; run `finwatch demo` for the complete deterministic artifact.

## Verified numbers

_Computed by versioned deterministic formulas from SEC XBRL facts (never by the LLM). Stale,
future-dated, malformed, unavailable, and not-applicable inputs remain explicit._

| Metric | Value | Computed as of | Formula | ✓ |
|---|---|---|---|---|
| Liquidity | cash $19.6B · net debt $25.3B · current ratio 1.24 | 2024-04-25 | `liquidity_basics.v2` | ✓ |
| Share count Δ | 0.0% YoY (share count flat) | 2024-04-25 | `share_count_change.v2` | ✓ |
| Net debt / (operating income + D&A) proxy | 1.35× · interest coverage 5.20× | 2024-04-25 | `simple_leverage.v2` | ✓ |
| Revenue growth | unavailable — current annual source is stale | 2024-04-25 | `revenue_growth.v2` | — |

## Open questions

_None._

## Boring filings

2 routine filings with no selected changes (AAPL 8-K, AAPL 10-Q).

---

_Educational analysis of public information for the portfolio owner's own decision-making. Not
individualized investment advice. Data may be incomplete or delayed._
