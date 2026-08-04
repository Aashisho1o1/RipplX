# RipplX provider contract

RipplX keeps providers narrow and optional. A provider failure may disable its feature, but cannot
change a compiler verdict or block unrelated SEC research.

| Provider | Purpose | Data sent | RipplX retention | Deletion / failure behavior |
|---|---|---|---|---|
| SEC EDGAR | Filings, submissions, and companyfacts | Operator contact in `User-Agent`; public ticker/CIK/accession | Public source documents, normalized facts, hashes, and provenance | Local cache/database reset removes copies. Missing or malformed source data becomes unavailable. |
| DeepSeek-compatible model endpoint | Bounded filing and connected-research tool decisions | Only required filing passages, structured metrics, agenda, and tool observations | Strict validated actions, attempt trace, and certificate inputs; never provider exceptions | Operator API key stays in process environment memory and never reaches the browser. Provider failure withholds the affected analysis or degrades the optional connected-research pass. |
| Resend | Login codes and notification email | Recipient email, subject, bounded plain-text message | Delivery status and stable dedupe key; no email body copy | Provider failure records `provider_failed` and remains retryable. |
| Stripe | Checkout and customer portal | Account email, opaque RipplX user reference, configured price ID | Customer/subscription IDs, price ID, and status; no card data | Stripe owns payment data. Verified webhooks update status; provider failure returns a fixed message. |
| PostHog | Small product-usage events | Hashed user ID and allowlisted short labels only | No separate local event store | Optional and nonblocking. Autocapture and session replay are not used. Financial/auth content is rejected by tests. |
| SnapTrade | Optional read-only brokerage connection | Not enabled in this build | Schema is reserved, but no secret is admitted | Activation is blocked until an audited encryption-at-rest dependency protects `userSecret`. Research remains fully usable without it. |

There is no market-price provider or valuation feature in the SEC-only prototype. Provider
selection, licensing, freshness guarantees, and valuation scope remain a later product decision.

## Hard exclusions

- PostHog never receives tickers, holdings, cost basis, thesis content, filing
  text, financial values, emails, or provider credentials.
- DeepSeek receives no unrestricted portfolio dump and cannot introduce facts outside bounded tools.
- Stripe receives no filing or investment-research content.
- Resend messages contain attention labels and reason codes, not private thesis text or holdings.
- SnapTrade, when activated, must use hosted authentication, request read-only access, and never
  collect brokerage passwords or trading permission.

Adding another provider requires a demonstrated product need and an update to this file, tests, and
the failure/deletion contract.
