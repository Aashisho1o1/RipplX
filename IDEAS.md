# IDEAS — parked explorations

Backlog of product ideas intentionally deferred. Nothing here is committed scope. Each entry
captures enough thinking to resume later without re-deriving it. Adding an entry is a decision to
*remember*, not a decision to build.

---

## Broker connect — import holdings, export your data

**One-liner:** Let a user connect a brokerage (Robinhood, Fidelity, Schwab, …) so finwatch learns
what they own instead of typing each ticker, and let them export what finwatch produced.

**Status:** Researched and deliberately NOT built (2026-07-27). Shape and vendor are decided if it
is ever picked up, but the cost/benefit does not justify building it at alpha scale — see "Why this
is parked again" below. No broker code exists in the tree.

### Two shapes — decided

1. **Tickers only** — **chosen.** Reduce the import to a distinct ticker set at the boundary, add to
   the watchlist, discard shares / cost basis / value. No schema change; preserves the whole trust
   architecture (finwatch keeps reasoning only about public SEC data per ticker).
2. **Full holdings + cost basis** — **rejected.** Needs a net-new user-scoped table (the deleted
   v0.2 `holdings` table was cik-keyed, not per-user — no prior art to reuse), lands net-worth data
   in plaintext-at-rest SQLite, and re-opens the portfolio-analytics / advice surface (RIA/GLBA)
   that §1 forbids and the lean cut removed. A different product with a different threat model.

Shape 1 delivers the actual user value — a populated watchlist — at a small fraction of the risk.

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

**Decision (2026-07-27): aggregator OAuth, hosted only, one-time import.** Robinhood has **no**
official third-party portfolio API; aggregators are the only legitimate automated route, and the
reverse-engineered `robin-stocks` (raw password + MFA, ToS violation, and now device approval plus
~24h sessions) stays disqualified. SnapTrade is the vendor: it is the only aggregator with
documented Robinhood coverage, has self-serve signup and no contract, and — decisively — its hosted
Connection Portal is a **top-level redirect**, which ships under the current CSP unchanged. A
Plaid-Link-style embedded SDK cannot load without weakening `script-src`, `default-src`, and
`connect-src` in `web/app.py`; for a trust-first product that outranks pricing.

CSV/paste was considered as a cheaper alternative input and also not built. If this is ever picked
up, one importer with two inputs — pasted symbols and aggregator positions — is the right shape;
paste has no instrument metadata so SEC resolution is its only gate, while aggregator positions
carry structured fields and need the stricter instrument filter applied *before* resolution.

### Why this is parked again (2026-07-27)

The blocker is not the $1/connected-user/month fee — at a 5–10 person alpha that is $5–10/month,
irrelevant. The real costs are SnapTrade production approval plus a compliance attestation, an
audit-on-request clause, GLBA Safeguards obligations, Robinhood being their flakiest integration
(24h connection expiry without 2FA), and a brokerage-credential-adjacent surface on a product whose
entire pitch is verified provenance.

Against that: the connector saves a user roughly one minute, once, versus pasting their ticker list.
There is no realistic 10-user alpha outcome where one-click Robinhood is what stands between this
product and evidence that it works. Revisit when there is real data that ticker entry is the
drop-off point, and at a user count where a saved minute aggregates into something.

### Export (ideation only)

finwatch doesn't hold "investment data" today, so export = what it *produced*: the watchlist
(round-trips with import), the verified digests (findings + exact evidence + six metrics +
derivations), and the certificates. JSON (complete) + CSV (metric table for spreadsheets).
Near-free — it serializes DTOs the API already builds. Pure data-portability upside,
open-source-native. This is the safe half and could ship independently of any import work.

### What exists in the tree

Nothing broker-specific. An importer core was built on 2026-07-27 and then deleted the same day as
premature — recover it from Git history (`8cf4d5b`, `6c21bed`) if this is picked up.

One thing was kept, because it was a real pre-existing bug rather than broker scaffolding:
registration resolves issuer identity against the current SEC index on **every** add instead of
trusting a stored `companies` row whose ticker matches. Symbols get recycled, so the old
short-circuit silently tracked the previous owner of a reassigned symbol. Recycled symbols now fail
closed with `TickerIdentityConflictError`, and share classes collapse to one issuer under an
order-independent label.

### If picked up: answer these before writing any route or client code

1. Is `userSecret` retrievable after issue? If it is returned once and cannot be looked up, a
   memory-only store orphans connections on a process restart, and the whole "no stored token"
   design needs rework. **This is the gating question.**
2. Exact structured `instrument_kind` tokens. The deleted `COMMON_STOCK_KINDS` allowlist was a
   conservative guess that failed closed; widening it is a trust decision, not a bugfix.
3. Consent evidence — does the portal supply it, or is a small consent record needed?
4. Billing when a connection is deleted immediately after a one-time import.
5. Whether persisting normalised ticker membership is permitted at all.

### Design notes for the remaining work

- Import runs **synchronously** under `app.state.company_add_lock`, not through `JobRegistry`:
  `JobRegistry.start` is a process-global mutex on a single worker, so one user's import would
  block every other user's sync and analysis.
- Return leg must be a **GET** redirect to our origin — `same_origin_mutations` 403s a cross-site
  form POST. `?connected=1` proves nothing; bind a nonce to the session and claim the connection
  once.
- Over the cap, let the user choose rather than taking an arbitrary subset. Keep planning pure and
  separate from writing, so the plan can be shown for confirmation first.
- Classify instrument kind BEFORE resolving a symbol. A crypto position in `ETH` resolves cleanly
  against an unrelated listed equity and would show the user that company's filings.

### Hard "no"s to carry forward
- No stored broker tokens in the prototype (one-time import only).
- No `robin-stocks` / reverse-engineered broker APIs, ever.
- No portfolio analytics / advice output ("you're up $X", "consider rebalancing") — that is the RIA
  line the launch must not cross.
- No quantities, cost basis, market values, or account identifiers — enforce this with a position
  type that has no such field, rather than by downstream discipline.
- Any wrong-issuer mapping is a stop-ship incident, not a bug to triage.

### The claim we may make

Not "we forget your brokerage entirely" — deletion is asynchronous and the vendor response
necessarily carries more than tickers. Instead:

> SnapTrade handles brokerage sign-in. RipplX temporarily processes the returned positions in
> memory, retains only selected issuer tickers, and requests deletion of the temporary connection
> after import. RipplX never stores brokerage credentials, quantities, balances, or cost basis.
