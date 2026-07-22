# finwatch — Open-Source Filing Intelligence for Self-Directed Investors
## Project Context & Operating Principles

Read this before touching code. It explains the product intent, the launch boundary, and the
principles the current code embodies. The code and tests remain the source of truth for details.

🔄 **Sync contract:** `AGENTS.md` and `CLAUDE.md` are byte-identical mirror copies. If one changes,
make the identical change to the other in the same commit. Tool-specific guidance belongs
elsewhere; do not let these files drift again.

The launch cut validates one promise: **add tickers; when the newest SEC filing arrives, show at
most three important changes, exact evidence, and six verified financial deltas.**

**Ground truth, in order:** (1) shipped code + tests, (2) this file, (3) `SYSTEM_DESIGN.md`. If a
current document disagrees with shipped behavior, fix the document.

## Project vision: lean, simple, and clean

RipplX / finwatch is one trust-first prototype, not a framework, compatibility warehouse, or
archive of past experiments. Keep the shipped path small enough to understand end to end: track a
ticker, sync its newest supported SEC filing, extract at most three qualitative findings with exact
evidence, compute six deterministic SEC-XBRL metrics, verify everything, and present one canonical
result.

- Git history is the archive. Delete unreachable, commented-out, speculative, superseded, and
  “maybe later” implementations instead of retaining dormant scaffolding in the active tree.
- Keep one current product path, one name for each active concept, and one source of truth for each
  contract. Avoid parallel schemas, duplicate persistence formats, compatibility aliases, and
  generic frameworks that serve no shipped behavior.
- A deletion is complete only when its imports, exports, types, database objects, repository
  methods, prompts, tests, fixtures, DTOs, API routes, UI code, documentation, configuration,
  dependencies, and CI references are removed or updated together.
- Add an abstraction, dependency, route, table, configuration switch, or background mechanism only
  when current prototype behavior needs it and a direct readable implementation is insufficient.
- Prefer explicit control flow, deterministic checks, fixed failure states, and fail-closed
  publication over speculative extensibility.
- Compatibility is opt-in for the prototype. Preserve it only when explicitly required, documented,
  and tested; otherwise back up data and make a clean break rather than carrying permanent migration
  baggage.
- Never commit commented-out implementations, temporary AI review notes, generated test output, or
  one-off debugging tools.

---

## ⚠️ Trust-critical code — read before touching any of these files

```
src/finwatch/llm/schemas.py           src/finwatch/verify/checks.py
src/finwatch/llm/stages.py            src/finwatch/verify/presentation.py
src/finwatch/xbrl/normalize.py        src/finwatch/presentation/canonical.py
src/finwatch/metrics/formulas.py      src/finwatch/core/types.py
src/finwatch/metrics/envelope.py      tests/test_verifier_mutations.py
```

These files are the deterministic trust layer: the extraction contract + server-side quote
anchoring, XBRL normalization, the six metric formulas, the V1/V4/V5 verifier, and the exact
canonical presentation. Edit them freely, but with extra care: their worst failure mode is a
silent, plausible error presented as verified.

1. Keep the full suite green (`uv run pytest -q`). When behavior changes, update the relevant
   mutation/edge-case test in the same commit and explain why.
2. Give every trust-layer change a real adversarial review. Prefer a test that would fail under the
   exact corruption being fixed.

---

## 1. Product definition

**One-liner:** Filing intelligence for self-directed investors: track tickers, review the newest
10-K/10-Q/8-K, see up to three AI-selected changes with exact SEC quotations, and inspect six
deterministically computed XBRL metrics.

**North-star user pain:**
> “I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something
> actually important changed.”

**What this is not:** an investment advisor, portfolio manager, trading system, valuation suite,
or historical backtester. The launch UI never emits a trade action, price target, P3 posture, or
shadow signal. Educational output supports the user's own decision.

**Trust promise:**

- The LLM selects and qualitatively summarizes a maximum of three findings. Every finding must be
  inseparable from one to three exact filing quotations. Finding headlines contain no numbers.
- Numbers shown as financial metrics come only from SEC XBRL and deterministic Python formulas.
- Numbers inside qualitative findings may appear only inside exact SEC quotations.
- Deterministic checks gate publication per finding. A bad finding is dropped with typed reason
  codes; surviving findings and deterministic metrics still publish. Whole-filing withholding is
  limited to provider/malformed-action breakdown or failed `FORM_SCOPE`/`CRITICAL_COVERAGE`.
- Verification proves provenance, exactness, schema, and hygiene. It does **not** prove that the
  model's interpretation of importance is correct, so the UI labels findings as AI-selected.

Prefer deterministic over stochastic · fewer/sharper findings over noisy coverage · explicit
`not_applicable`/`unavailable` over guessing · withholding over plausible unsupported output.

---

## 2. History

The v0.2 backend implemented a broader seven-stage research system (P2 portfolio-impact analysis,
P3 signal rationale + the signal matrix + shadow logs, extended valuation/scoring metrics —
Piotroski/Altman/Beneish/PEG/Graham/percentiles, Stooq prices, position sizing/holdings accounting).
**That code has been deleted in the lean cut.** Recover any of it from Git history if a future
product decision justifies it — do not reintroduce dormant scaffolding into the active tree (see
"Project vision" above). Any return to a larger scope needs a fresh threat/correctness review and an
explicit product decision.

---

## 3. Current launch architecture

```
EDGAR filing index + primary document ──► download ──► P0 canonical sections/hashes
SEC companyfacts ──► point-in-time XBRL normalization ──► six starter metrics + rounding slack

section catalog + metrics ──► bounded P1 JSON tool loop ──► compiler ──► one shared repair
                          ──► finance Skeptic ──► final compiler/per-finding prune

surviving P1 + metrics + stored sections ──► deterministic publication gate
                                  ├─ V1 numeric provenance
                                  ├─ V4 exact citation integrity
                                  ├─ V5 schema/advice/hygiene
                                  └─ V2 XBRL identities (warning only; never an LLM gate)
                                  ──► exact canonical FilingDigestEntry DTO
                                      ├─ browser API/React
                                      ├─ deterministic Markdown serializer
                                      └─ verification certificate + compact tool trace
```

The persisted pipeline ledger has exactly five current stages:
`download → parse → metrics → extract → verify`.

P3 is not constructed, so V3 rule re-derivation is `skipped_not_applicable`. P2 is not constructed
or called. P1 is one bounded provider-neutral harness: a Generator may call five deterministic
tools, a finance Skeptic may add only finding-local objections, and the compiler is the sole judge.

### Launch scheduling and retries

- Automatic analysis considers the newest supported 10-K/10-Q/8-K in scope. A user may narrow a
  run to one of those three form families; amendments remain in their base-form family.
- If that newest filing is already `verified` or terminally `analyzed`/withheld, the run is a no-op.
  It never falls through to an older filing. When a requested run has nothing to do, it reports
  exactly one of three fixed reasons: no supported filing has been synced, no filing of the selected
  form family has been synced, or the newest supported filing has already been analyzed. Job items
  never carry caller-supplied display text.
- Every production retry is a fresh full attempt: download, parse, metrics, extract, verify. No
  parse-only/extract-only user control and no mixing of artifacts from different attempts.
- A failed newest filing gets at most two persisted full-pipeline attempts, counted from
  `download`; missing URLs and fetch failures consume attempts so one issuer cannot starve the
  portfolio. One harness attempt has eight Generator turns, six Generator tool requests, one
  shared repair, and two Skeptic tool requests; every action is strict JSON and every loop is
  bounded.
- The in-process job runner has one worker. Jobs are ephemeral across process restarts; this is an
  accepted alpha limitation, not a durable-queue claim.

---

## 4. Tech stack and working conventions

**Approved stack:** Python ≥3.11 + uv · SQLite (`sqlite3` + retained FTS5) · Typer · pydantic v2 ·
litellm · httpx + tenacity · selectolax · pyyaml · rich · FastAPI/uvicorn · React/TypeScript/Vite ·
pytest/ruff. Apache-2.0, GitHub Actions CI, and conventional commit prefixes (`feat:`, `fix:`,
`test:`, `chore:`). Ask before adding a dependency.

**EDGAR etiquette:** identify with a real contact in `SEC_USER_AGENT`; throttle to ≤8 requests/s;
back off on 429/403; cache immutable filings. Filing text and EDGAR metadata are untrusted input.

**Standing rules:**

- Plan → implement → focused tests → full suite → conventional commit. Never knowingly leave the
  suite red.
- No live network or LLM calls in ordinary tests. Use recorded fixtures; mark optional smoke tests
  `@pytest.mark.live`.
- Prompts are versioned data under `src/finwatch/prompts/*.md`; never inline them in Python.
- Every `className` literal under `web/src` must resolve to a selector defined in
  `web/src/styles/*.css`; `npm run check:classnames` enforces this in CI. Delete the class name or
  define the rule — never leave an orphan.
- Never weaken `prompts/foundation.md`: filing content is data, not instructions.
- Preserve unrelated user changes in a dirty worktree. Bump the one fresh schema version and require
  backup/reset rather than building compatibility migrations.
- Treat exception/provider text, holdings, the SEC contact email, and API keys as sensitive. Never
  return raw diagnostics or secrets through an API.

---

## 5. User and operator surfaces

The browser app is the launch surface. Hosted onboarding is public email-code login followed by a
ticker; local mode remains auth-free. It does not ask for or return shares, cost basis, target
weights, horizon, or thesis.
The watchlist reports the newest supported 10-K/10-Q/8-K indexed for an issuer; it never reports an
unsupported form and never claims a filing was read.

The browser also serves a read-only sample brief at `?demo=1` in both local and hosted mode. It is
built from bundled public SEC fixtures by the real pipeline with recorded model output, lives in a
throwaway in-memory database created per request, and is always projected as the reserved local user
so it can never read or mix with a participant's own data.

Current CLI commands are `init`, `serve`, `add`, `analyze`, `ingest`, `process`, `metrics`,
`digest`, `eval`, and `demo`. Treat the CLI as operator/developer tooling, not a second product.
`eval` is internal model-evaluation tooling. `demo` must remain zero-key and fast.

Analysis and process commands use the same newest-only production runner. There is no `verify`,
`reverify`, `shadow report`, signal flag, replay mode, accession selector, or analysis limit control
in the launch surface. The browser may narrow the newest-only run to 10-K, 10-Q, or 8-K; it never
selects an accession or falls through to older history within that form.

---

## 6. SQLite and stored data

Authoritative DDL is the one fresh schema in `src/finwatch/db/schema.sql`; there is no migration
ladder. Schema v6 is a clean backup-and-reset boundary for attempt-linked `harness.v2` traces and
`certificate.v2`; no v1 compatibility fallback exists. It stores public users, private user-company
membership, and one private period preference per user. A reserved local user preserves CLI/local
behavior. Issuers, filings, SEC facts, analyses, computations, and verification remain shared
public-data artifacts. Older schema versions fail closed with a backup-and-reset message.

For the web app, schema install/verification runs synchronously once during `create_app`; request and
background-job connections use `connect()` and never install schema. Operational connections enable foreign keys,
WAL, and a 5-second busy timeout. New POSIX data directories are `0700`; database files are `0600`.
Replace-style writes for XBRL, filing sections/FTS, and verifier results are transactional.

Sensitive local data includes account emails, private tracked-ticker membership/preferences, the
persisted local SEC User-Agent/contact email, and filing/metric data. SQLite and the data volume are
plaintext; filesystem/container access is the data-at-rest boundary. Hosted participant provider
keys are session-keyed process memory only, never SQLite fields, cookies, logs, or API responses.

---

## 7. P0 and source provenance

The runner downloads the trusted SEC URL selected by ingest. P0 then detects the form and stores
canonical section text, section-relative offsets, element IDs, furnished/amendment metadata, and
full text hashes. P1 never guesses section boundaries.

Recurring canonical keys:

| Form | Filing location | Key |
|---|---|---|
| 10-K | Item 1 / 1A / 3 / 7 / 7A / 8 / 9A | `business`, `risk_factors`, `legal`, `mdna`, `market_risk`, `financials`/`auditor_report`/`notes`, `controls` |
| 10-Q | Part I Item 2 / 3 / 4 | `mdna`, `market_risk`, `controls` |
| 10-Q | Part II Item 1 / 1A | `legal`, `risk_factor_changes` |
| 8-K | each present item | `item_<number>`, e.g. `item_4_02` |

Risk-factor diffs remain deterministic preprocessing input. Amendments link to the prior accession
where possible. Furnished Item 2.02/7.01 content is labeled; routine furnished earnings should
normally produce no launch finding.

---

## 8. XBRL and the six starter metrics

`FactStore.from_companyfacts()` normalizes SEC companyfacts. Headline concepts use consolidated
facts, align duration versus instant periods per accessor, respect units/decimals, and apply
amendment supersession. Facts without a provable `filed <= as_of` date are excluded. Current annual
source legs must be no more than 550 days old; instant, share-count, and optional quarterly source
legs must be no more than 200 days old. Future, stale, missing, or malformed source dates become
explicit unavailable states rather than masquerading as current.

Production computes and persists only:

1. `revenue_growth`
2. `net_income_trend`
3. `cfo_trend`
4. `liquidity_basics`
5. `share_count_change`
6. `simple_leverage`

Every result uses the universal `computed | unavailable | not_applicable` envelope, formula version,
effective `as_of`, and source inputs, including SEC `decimals`. Revenue, net income, CFO, and share
count also carry `direction_delta`, conservative `direction_slack`, and `direction_basis`; missing
rounding metadata never becomes zero slack. The browser DTO carries the source computation ID. The
full formula catalog remains tested research code but is not called, persisted, or presented by the
launch metrics service. Price, valuation, and holding/portfolio inputs are absent.

Presentation language stays narrower than the accounting facts: share-count changes are described
only as increased, decreased, or flat—not inferred to be dilution or a buyback—and only when the
rounding-aware direction is proved. When `deterministic_direction` is unavailable the row states the
signed change and says the direction is not certified within SEC rounding slack, so the table never
asserts in certified wording what the compiler would drop as `METRIC_DIRECTION_UNAVAILABLE`.
`simple_leverage` is explicitly labeled as a net-debt / (operating income + D&A) proxy, never
reported EBITDA. User-facing dates say “computed as of”; `effective_as_of` remains only an internal
DTO field name. The metric envelope itself remains three-valued. Presentation adds one further row
state, `withheld`, for a persisted starter computation that fails presentation-time provenance
re-validation; such a row is rendered from trusted database columns only, never from the payload
that failed, and it still counts against the fixed six-metric denominator. Metric rows are never
silently dropped, and a metric table is rendered whenever any starter row exists — an all-unavailable
issuer must be distinguishable from an issuer that was never synced.
Validated metric rows also project their persisted formula expression and SEC XBRL inputs for an
expandable browser derivation; a row withheld by provenance re-validation exposes no derivation.

---

## 9. P1 extraction and LLM boundary

`P1Output` is a strict pydantic contract (`extra="forbid"`): filing identity, severity, zero to
three uniquely identified findings, extraction confidence, and nonblocking gaps. Each finding uses
`f1`/`f2`/`f3`, a controlled severity/critical flag, and one to three exact quotations of at most 50
words. Optional `metric_id` and `direction` fields must appear together; the compiler compares them
against the registry metric's rounding-aware delta. Offsets are server-derived relative to the
named canonical section and must satisfy
`section_text[start:end] == snippet`.

Medium/high/critical classifications require a finding; an empty finding list is legitimate only
for low/routine filings. Canonical 8-K Items 1.03, 2.04, 3.01, and 4.02 deterministically require the
corresponding critical, section-backed finding; prompt/evaluation rules cover semantic
going-concern, auditor, control, and material-cyber cases. No evidence means no finding. Echoed
accession, ticker, and form must match trusted filing metadata before persistence.

The initial P1 prompt contains only trusted filing/section/metric catalogs. Current-versus-prior
change ranges are precomputed before the model acts, so compiler validity never depends on whether
the model called `get_changes`. The Generator retrieves bounded evidence progressively through
`search_sections`, `get_changes`, `get_metric`, `get_accounting_checks`, and one `check_draft`
preflight; duplicate calls are cached but still spend budget. Generator and Skeptic turns/tools use
independent attempt-wide counters, and every advertised remaining budget is nonnegative. Every turn
receives the agenda, validated observations, compiler errors, and remaining budget. A second model
pass is a one-directional finance Skeptic: it may call the four read tools and add typed objections
to a specific finding, but never author, approve, or promote a finding. Once a compiler-passing
baseline exists, optional Skeptic/repair protocol or budget failure preserves clean findings and
drops only findings carrying validated objections; provider failure remains a whole-run failure.
Output is capped at 2,000 tokens per call.

Production accepts one `FINWATCH_MODEL` using the `openai/`, `openrouter/`, or `z-ai/` prefix, with an
optional `FINWATCH_SKEPTIC_MODEL` on the same provider (otherwise it reuses the Generator), and the
matching `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, or `ZAI_API_KEY`. Each prefix maps to exactly one
fixed endpoint — `z-ai/<model>` routes to Zhipu GLM through z.ai's Anthropic-compatible endpoint
(`https://api.z.ai/api/anthropic`), where the Anthropic API has no JSON-object response format so the
prompt carries the JSON contract instead. Arbitrary providers and caller-supplied base-URL overrides
stay out of the launch path. Broader provider/model flexibility inside dormant developer utilities is
not a production configuration promise. CLI/local keys may come from the environment or browser
session memory. Hosted participants must provide their own matching key; hosted requests ignore
environment provider keys. Keys must never be logged, persisted, placed in cookies/browser storage,
or returned.

---

## 10. Verification and fail-closed publication

The verifier is deterministic and never edits content to make a check pass.

- **V1 numeric provenance:** the shared authored-text policy rejects quantities, trade instructions,
  price targets, first-person valuation, and forbidden vocabulary from P1-authored headlines.
  Numeric filing content remains allowed inside declared exact evidence spans; displayed metric
  numbers come from persisted starter computations. V1 and V5 judge the same unit the compiler
  judges — one authored headline and one rendered line at a time — so a violation is always
  attributable to a single finding and is pruned rather than failing the run.
- **V4 citation integrity:** accession, section, bounds, exact substring, and stored source text are
  checked.
- **V5 schema and hygiene:** strict P1 schema, disclaimer, no trade instructions/price targets, and
  forbidden vocabulary.
- **V2 accounting identities:** non-blocking XBRL data-quality warnings only. They may populate open
  questions and are labelled non-blocking wherever they are displayed; every non-failing V2 result
  carries informational severity so no surface can present an accounting identity as a gate.
  Regenerating P1 cannot repair source accounting data.
- **V3:** not applicable because no P3 decision exists in launch.

Before the legacy publication checks, `verify/compiler.py` deterministically anchors evidence,
applies the same authored-headline policy used by V5 and final DTO verification, enforces precomputed
changed-span support where applicable, and checks structured metric direction. After the shared
repair, findings with local error codes are pruned and classification is recomputed; no survivors
means a routine metrics-only publication. Dropping a required critical finding fails
`CRITICAL_COVERAGE` and withholds the filing.

Publication additionally requires a `verified` filing status, persisted passing V1/V4/V5 rows, and
no blocking failure. `presentation/canonical.py` then constructs the exact `FilingDigestEntry` used
by both surfaces: it accepts at most three findings, attaches the trusted SEC URL and actual full
section SHA-256, rechecks exact offsets/quotes, rejects duplicate/ambiguous evidence, and runs
`verify/presentation.py` over the final DTO. Candidate-local projection errors drop only that
candidate; identity, persisted-gate, or final-DTO failures withhold the filing. Browser projections
and `/api/jobs` expose only fixed safe messages; persisted raw stage details are never projected.

The final P1 stays in stage `P1`; its strict `harness.v2` trace stays in `P1_TRACE`. P1 and trace are
inserted atomically, linked by analysis ID and the SHA-256 of the exact serialized P1 bytes, then
verification rows, the frozen final trace snapshot, filing status, and processed time are finalized
in one transaction. Presentation resolves P1 only through the latest valid finalized v2 trace.
`GET /api/filings/{accession}/certificate` returns an owner-scoped `certificate.v2` built solely
from that immutable attempt snapshot. Verified and completed-withheld (`analyzed`) attempts receive
certificates; withheld certificates remove classification, evidence, published IDs, prose, tool
arguments, and verification details before hashing. Pending, failed, malformed, mismatched, or v1
attempts have no certificate. The filing UI shows the persisted verification roll-up — one row per
persisted check with its check id, a fixed human label, and its verdict — alongside the tool count,
compact trace, dropped codes, and conditional download. V1/V4/V5 are shown as the blocking
publication gate; V2 is shown as explicitly non-blocking data quality and is the only check family
whose persisted detail string is projected, because V1/V4/V5 details quote model-authored text. Raw
model output, gated-check details, and provider exceptions never cross the API boundary.

---

## 11. One canonical presentation path

`PresentationService` is the sole database-to-user-content projection. React consumes its pydantic
DTO through FastAPI. `digest/render.py` serializes that same `BriefView`; it does not independently
reload analyses, claims, or computations. Filing/LLM text is rendered as escaped text, not raw HTML
or caller-supplied Markdown. The brief carries no posture, tone, or sentiment field: `answer` is a
plain sentence and the UI applies no severity-derived colour to it.

The launch output is deliberately explicit:

- “AI-selected changes (evidence verified)” separates model judgment from deterministic evidence
  validation;
- exact quotations are proved by the section key, offsets, and section hash shown beside them, and
  link to the HTTPS SEC filing index page for that accession;
- the brief states the reading window it applied in human dates, reports in-window filings against
  the unfiltered tracked total, and names any tracked filing that passed the gate but falls outside
  the window;
- the filing page names each deterministic check that ran, its verdict, and whether it was blocking;
- verified numbers show state, formula version, effective date, and computation provenance;
- the at-most-three finding cap and the fixed six-metric catalog are stated on the surfaces that
  show them, and a `partial` publication is presented as the per-finding gate succeeding, never as a
  failure;
- routine filings are a valid result and publish as identified, linkable “Reviewed — nothing
  material” entries, not as an unlinked summary sentence;
- each `FilingDigestEntry` carries an explicit `outcome` (`published`, `no_findings`,
  `findings_dropped`, `withheld_gate`, `pipeline_failed`, `not_analyzed`) and a
  `dropped_finding_count`, so a run whose candidate findings were all pruned is never announced as
  “nothing important changed”;
- a pipeline failure (`withheld_kind = pipeline_failed`) is presented in its own bucket with fixed
  copy and is never described as a gate refusal or a verification outcome;
- brief period counts separate attempts with an analysis on file (`analyzed_filings`) from attempts
  that cleared the gate (`published_filings`) and attempts held back (`withheld_filings`);
- the brief reports how many supported filings have actually been synced, so an unsynced watchlist
  and a synced-but-unanalyzed watchlist get different next steps in both the browser and
  `digest/render.py`;
- withheld filings never expose the failed LLM output.
- interactive control boundaries and focus indicators are held to WCAG 1.4.11 / 2.4.11 (3:1) by
  `web/src/styles/styles.test.ts`, while decorative hairlines stay deliberately faint.

---

## 12. Web security and operations

Local serving binds loopback by default and rejects a non-loopback bind without `--allow-remote`.
A hosted alpha additionally requires `FINWATCH_AUTH_SECRET` of at least 32 characters, Resend email
configuration, the operator `SEC_USER_AGENT`, and an explicit `FINWATCH_ALLOWED_HOSTS` allowlist;
wildcard hosts are rejected and remote API docs are disabled. Signup is public: any valid email may
request a six-digit code. Codes expire after ten minutes, allow five attempts, and live only in one
process with small per-email/global send limits. Successful verification creates/fetches a user and
sets a signed 30-day `HttpOnly`, `Secure`, `SameSite=Lax` cookie containing only opaque IDs/expiry.
Cookie-authenticated mutations require a separately signed double-submit CSRF token and exact
Origin. Local mode is auth-free; CORS is restricted to local dev origins.

Responses set CSP, frame denial, nosniff, no-referrer, API `no-store`, and HSTS in remote mode.
Request bodies are capped at 1 MiB for both declared-length and chunked streams. Server-side checks
scope watchlists, briefs, filing/metric reads, preferences, mutations, and job polling to the current
user; cross-user private resources return 404. Public SEC-derived artifacts remain shared. The single
owner-tagged job registry strips diagnostics, allowlists verdicts, stages, and a closed set of typed
no-op reason codes, and returns only fixed user-safe failure messages; caller-supplied item text is
always discarded; unhandled API errors use a generic JSON contract. Decoded EDGAR responses
are capped at 64 MiB before cache writes. There is no durable queue, multi-instance coordination,
team/role model, or persistent session registry.

Sessions are stateless. Logout removes the browser cookie and that session's in-memory provider key,
but a copied signed cookie remains valid until its 30-day expiry or signing-secret rotation. This is
an accepted prototype consequence of omitting a persistent session registry.

Back up the SQLite data directory before upgrades. `/healthz` is public and intentionally shallow;
it proves process liveness, not EDGAR/model/database end-to-end health.

---

## 13. Golden set and launch acceptance

The golden set under `src/finwatch/evals/golden_set/manifest.yaml` contains 12 real accession-pinned
cases: critical, boring, and routine filings. Recorded fixtures run with no network/key; optional
live evaluation fetches the pinned SEC primary document. Scoring includes critical-flag recall, JSON
validity, verifier/canonical-projection pass rate, false alarms, tokens, and cost.

Critical recall must remain 100%; a missed going-concern/non-reliance/bankruptcy/delisting/material
cyber event is disqualifying. Boring filings must not scream. Model bake-off is developer tooling,
not a multi-provider production surface.

---

## Roadmap — out of scope unless explicitly requested

**Pinned next iteration after harness validation:** bounded `resolve_fact` Tier 0→1 only, triggered
by an existing metric's missing precondition. It may admit at most two facts from the current SEC
filing after exact-span, deterministic parse, unit/scale/period, input-contract, and redundant-
derivation checks; no web/IR/news/market sources.

Otherwise deferred: P2/P3 or signal reactivation · broker import/sync · portfolio accounting · Form 4 tracking · news ·
earnings calls · sector-relative valuation · durable distributed jobs · teams/roles · historical
analysis replay · provider-native function calling · generic plugins/subagents · bitemporal redesign
· financial mathlib · Lean/Z3/SMT/AI-Hilbert · deeper symbolic reasoning.

## Where to find things

| Topic | Authoritative source |
|---|---|
| Launch status and quickstart | `README.md` |
| Current module/data-flow contracts | `SYSTEM_DESIGN.md` |
| DB DDL (single fresh schema, no migrations) | `src/finwatch/db/schema.sql`, `db/database.py` |
| Harness/compiler/prompts | `src/finwatch/llm/harness.py`, `verify/compiler.py`, `prompts/P1_*.md` |
| Starter metric catalog/service | `src/finwatch/metrics/catalog.py`, `service.py` |
| Launch pipeline and scheduling | `src/finwatch/pipeline/orchestrator.py`, `run.py`, `progress.py` |
| Publication checks | `src/finwatch/verify/compiler.py`, `checks.py`, `verify/presentation.py` |
| Canonical projection and renderer | `src/finwatch/presentation/`, `src/finwatch/digest/render.py` |
| Web/API/security | `src/finwatch/web/`, `web/` |
| 12-case golden set | `src/finwatch/evals/golden_set/manifest.yaml` |

## Review discipline

Be adversarial toward your own work. Do not validate an implementation merely because it is elegant
or because an AI produced it. Trace the real runtime path, test the failure mode, distinguish launch
code from dormant research, and optimize for user trust and early learning rather than feature count.

— end —
