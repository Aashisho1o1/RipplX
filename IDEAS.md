# IDEAS — parked explorations

Backlog of product ideas intentionally deferred. Nothing here is committed scope. Each entry
captures enough thinking to resume later without re-deriving it. Adding an entry is a decision to
*remember*, not a decision to build.

---

## Broker connect — import holdings, export your data

**One-liner:** Let a user connect a brokerage (Robinhood, Fidelity, Schwab, …) so finwatch learns
what they own instead of typing each ticker, and let them export what finwatch produced.

**Status:** Decided and partially built (2026-07-27). **Shape 1 (tickers only) chosen, permanently.**
Mechanism: aggregator redirect portal (SnapTrade), hosted deployment only. The importer core and the
issuer-identity fixes it depends on are in the tree; routes, vendor client, and UI are blocked on a
vendor spike (see "Blocked on" below).

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

**Ticker paste shipped (2026-07-27)** as the first input to the importer, reversing the earlier
hosted-only-no-fallback call. It is one importer with two inputs, not a second architecture:
`plan_symbols` and `plan_import` share the resolve-and-dedupe core and both produce an `ImportPlan`.
It earns its place three times over — it gives the importer a real caller today, it is the
network-free end-to-end path, and it is the control experiment for whether an aggregator materially
improves activation over pasting a list. It is also the outage and unsupported-broker fallback.

Paste carries no instrument metadata, so SEC resolution is its only gate — the same gate a
hand-typed ticker already passes. Aggregator positions do carry structured metadata and get the
stricter instrument filter. No CSV, no quantities, no account export.

**Watch, do not build on:** since 2026-05-27 Robinhood operates a first-party OAuth MCP endpoint
(`agent.robinhood.com/mcp/trading`, PKCE + dynamic client registration) granting read access to
positions. Beta, invite-gated, desktop-only auth, scope literally named `internal`, no published
developer terms. If real terms appear it beats any aggregator and removes the per-user fee.
Re-check quarterly.

### Export (ideation only)

finwatch doesn't hold "investment data" today, so export = what it *produced*: the watchlist
(round-trips with import), the verified digests (findings + exact evidence + six metrics +
derivations), and the certificates. JSON (complete) + CSV (metric table for spreadsheets).
Near-free — it serializes DTOs the API already builds. Pure data-portability upside,
open-source-native. This is the safe half and could ship independently of any import work.

### Built so far

- `src/finwatch/broker/` — `plan_symbols` (pasted symbols), `plan_import` (aggregator positions,
  with the instrument filter), and `apply_plan` (track, fill to the cap, report the remainder).
  Covered by `tests/test_broker_*.py` with no network.
- `POST /api/companies/import` plus the **Paste tickers** panel on the watchlist — the importer's
  production caller.
- Two rules the importer exists to enforce. *Classify before resolving*: a crypto position in `ETH`
  resolves cleanly against an unrelated equity in the SEC index, so instrument kind decides first,
  from structured vendor fields only. *Default deny*: an unrecognised kind, missing exchange, or
  non-USD currency is `unsupported_instrument`, never tracked. v1 admits US-listed common stock
  only.
- Identity fixes this depended on (shipped separately, valuable on their own): registration resolves
  issuer identity from the SEC index on **every** add rather than trusting a stored ticker row —
  the old short-circuit silently tracked the previous owner of a recycled symbol. Recycled symbols
  now fail closed with `TickerIdentityConflictError`; share classes collapse to one issuer under an
  order-independent label.

### Blocked on — a vendor spike, before any route or client code

1. Is `userSecret` retrievable after issue? If it is returned once and cannot be looked up, a
   memory-only store orphans connections on a process restart, and the whole "no stored token"
   design needs rework. **This is the gating question.**
2. Exact structured `instrument_kind` tokens. `COMMON_STOCK_KINDS` in `broker/symbols.py` is a
   conservative guess that fails closed; widening it is a trust decision, not a bugfix.
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
- Over the cap, let the user choose. `plan_import` is pure precisely so the plan can be shown for
  confirmation before anything is written.

### Hard "no"s to carry forward
- No stored broker tokens in the prototype (one-time import only).
- No `robin-stocks` / reverse-engineered broker APIs, ever.
- No portfolio analytics / advice output ("you're up $X", "consider rebalancing") — that is the RIA
  line the launch must not cross.
- No quantities, cost basis, market values, or account identifiers — enforced by the shape of
  `BrokerPosition`, which has no such field, rather than by downstream discipline.
- Any wrong-issuer mapping is a stop-ship incident, not a bug to triage.

### The claim we may make

Not "we forget your brokerage entirely" — deletion is asynchronous and the vendor response
necessarily carries more than tickers. Instead:

> SnapTrade handles brokerage sign-in. RipplX temporarily processes the returned positions in
> memory, retains only selected issuer tickers, and requests deletion of the temporary connection
> after import. RipplX never stores brokerage credentials, quantities, balances, or cost basis.
