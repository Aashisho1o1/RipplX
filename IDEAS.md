# IDEAS — parked explorations

Backlog of product ideas intentionally deferred. Nothing here is committed scope. Each entry
captures enough thinking to resume later without re-deriving it. Adding an entry is a decision to
*remember*, not a decision to build.

---

## Broker connect — import holdings, export your data

**One-liner:** Let a user connect a brokerage (Robinhood, Fidelity, Schwab, …) so finwatch learns
what they own instead of typing each ticker, and let them export what finwatch produced.

**Status:** Parked (2026-07-24). Explore only after the current launch ships and is stable. Do not
let this delay deploying what already works.

### Two shapes — choose deliberately when we revisit

1. **Tickers only** — reduce the import to a distinct ticker set at the boundary, add to the
   watchlist, discard shares / cost basis / value. No schema change; preserves the whole trust
   architecture (finwatch keeps reasoning only about public SEC data per ticker). Cheap and safe —
   effectively "add a ticker" in bulk.
2. **Full holdings + cost basis** *(the direction chosen to explore)* — store shares, cost basis,
   value per user. The ambitious version, and not free: needs a net-new user-scoped table (the
   deleted v0.2 `holdings` table was cik-keyed, not per-user — no prior art to reuse), lands
   net-worth data in plaintext-at-rest SQLite, and re-opens the portfolio-analytics / advice
   surface (RIA/GLBA) that §1 forbids and the lean cut removed. Gate behind a real security +
   regulatory review before any code.

Honest trade: shape 1 is a weekend feature that changes nothing about the product's promise; shape
2 is a different product with a different threat model. If shape 2's cost is too high when we
revisit, shape 1 still delivers ~80% of the user value (a populated watchlist) at ~5% of the risk.

### Import mechanism — CSV/paste vs aggregator OAuth

| | CSV / paste | Aggregator OAuth (Plaid Investments / SnapTrade) |
|---|---|---|
| Dependency | None | Paid B2B contract + approval |
| Build cost for us | Parse a column | OAuth app + provider integration + secret handling |
| Robinhood | Works (user exports CSV) | Works (both aggregators support RH) |
| Broker coverage | Any broker that can export | Only institutions the aggregator supports |
| User friction | Export a file, upload/paste | One click |
| Tokens stored | None | An access token — must be discarded (one-time import) or it becomes the single largest liability in the system; storing it breaks the "secrets never in SQLite" invariant |
| Freshness | Manual re-import | Can re-sync — but that needs a stored token → liability |
| Failure mode | Bad column mapping | Provider outage, revoked consent, breaking API changes |
| ToS / legal | Clean (user's own file) | Clean via aggregator; NEVER the reverse-engineered robin-stocks path |

**Read:** CSV/paste is the lean, dependency-free, works-with-everything foundation and the right
first move; aggregator OAuth is the one-click polish for later, and only ever as a one-time import
(no stored token). Robinhood has **no** official third-party portfolio API — aggregators are the
only legitimate automated route; the reverse-engineered `robin-stocks` (raw password + MFA, ToS
violation, fragile) is disqualified for a trust product.

### Export (ideation only)

finwatch doesn't hold "investment data" today, so export = what it *produced*: the watchlist
(round-trips with import), the verified digests (findings + exact evidence + six metrics +
derivations), and the certificates. JSON (complete) + CSV (metric table for spreadsheets).
Near-free — it serializes DTOs the API already builds. Pure data-portability upside,
open-source-native. This is the safe half and could ship independently of any import work.

### Where to start when we revisit
- Single choke point: `IngestService.track_company` (`src/finwatch/ingest/service.py`) resolves a
  ticker→CIK and writes `companies` + `user_companies`. A bulk importer loops it, or a `track_many`
  batches one `company_tickers()` fetch instead of refetching per ticker. Idempotent `ON CONFLICT`
  upsert — re-import is safe.
- New bulk route parallels `create_company` (`src/finwatch/web/app.py`); reuse `company_add_lock`
  and re-evaluate the `MAX_TRACKED_TICKERS = 25` cap for batch (partial-import semantics).
- Ticker resolution: `resolve_ticker()` (`src/finwatch/ingest/tickers.py`) against SEC
  `company_tickers.json`.
- Tickers-only shape needs no schema change. Full-holdings shape needs a new user-scoped table plus
  the security review above.

### Hard "no"s to carry forward
- No stored broker tokens in the prototype (one-time import only).
- No `robin-stocks` / reverse-engineered broker APIs, ever.
- No portfolio analytics / advice output ("you're up $X", "consider rebalancing") — that is the RIA
  line the launch must not cross.
