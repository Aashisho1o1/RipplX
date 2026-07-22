# finwatch — SYSTEM_DESIGN.md
## Launch architecture, module map, and integration contracts

This document maps the narrow launch runtime. It sits below the code/tests and the mirrored
`AGENTS.md`/`CLAUDE.md` context files.

> **Precedence:** shipped code + tests > `AGENTS.md`/`CLAUDE.md` > this map.

The lean cut deleted the v0.2 research code (P2/P3, signals, extended formulas, prices, holdings);
the runtime wiring in `pipeline/run.py` is authoritative. Recover removed code from Git history.

---

## 1. Criticality tiers

Tiers describe review depth, not edit permissions.

- **⚙ Tier 1 — trust-critical, test-guarded.** Silent corruption can create a plausible verified
  result. Changes need targeted mutation/edge tests plus the full suite.
- **🔧 Tier 2 — launch contracts.** These modules join trust boundaries. Change both producer and
  consumer together and test the exact user DTO.
- **🧱 Tier 3 — standard application code.** Ordinary engineering under the repository working
  conventions.

Tier 1 files:

```
src/finwatch/core/types.py            src/finwatch/signals/matrix.py        (dormant)
src/finwatch/metrics/envelope.py      src/finwatch/verify/checks.py
src/finwatch/xbrl/normalize.py        tests/test_signals_matrix.py          (dormant spec)
src/finwatch/metrics/formulas.py      tests/test_verifier_mutations.py
```

The dormant signal matrix remains Tier 1 because accidental reactivation or future promotion would
make its silent decisions consequential.

---

## 2. Annotated module map

```
src/finwatch/
├── ingest/                   EDGAR client, ticker→CIK, filing/companyfacts cache
├── preprocess/               P0 form router, canonical sections, offsets, hashes, diffs
├── llm/
│   ├── schemas.py            strict finding + structured-direction contract
│   ├── harness.py            bounded JSON tools/agenda/repair/Skeptic/trace
│   ├── stages.py             thin P1 harness facade
│   └── router.py             injected client; LiteLLM implementation
├── prompts/
│   ├── foundation.md         prompt-injection and evidence rules
│   ├── P1_extractor.md       Generator action/tool protocol
│   ├── P1_skeptic.md         one-directional finance objection protocol
│   └── P2_impact.md, P3_rationale.md                 dormant research
├── xbrl/                     point-in-time SEC companyfacts normalization
├── metrics/
│   ├── catalog.py            six launch metrics and labels
│   ├── service.py            launch-only compute/persist orchestration
│   ├── envelope.py           universal result/provenance envelope        ⚙
│   └── formulas.py           starter + dormant extended pure formulas    ⚙
├── pipeline/
│   ├── run.py                newest-only production wiring/scheduling
│   ├── orchestrator.py       P0→metrics→tool harness→publication gate
│   ├── progress.py           five-stage persisted ledger
│   └── adapters.py           dormant P2/P3/matrix adapters
├── verify/
│   ├── compiler.py           finding-local compile/repair/prune + direction gate ⚙
│   ├── checks.py             deterministic V1–V5 primitives              ⚙
│   ├── orchestrator.py       report persistence + non-blocking V2 audit
│   └── presentation.py       exact final FilingDigestEntry verifier
├── presentation/
│   ├── projection.py         persisted verification fail-closed gate
│   ├── canonical.py          trusted SEC identity + exact evidence DTO
│   ├── models.py             browser/Markdown pydantic contracts
│   └── service.py            sole database→user-content projection
├── digest/render.py          pure Markdown serialization of BriefView
├── web/
│   ├── app.py                FastAPI, auth/origin/body limits, service wiring
│   ├── auth.py               public email OTP + signed session/CSRF primitives
│   ├── jobs.py               one-worker owner-scoped safe job registry
│   ├── runtime.py            per-user settings + session-keyed memory secrets
│   └── security.py           remote signing/email/host configuration policy
├── db/                       one fresh SQLite schema + repositories
├── evals/                    12-case accession-pinned golden set/harness
├── signals/                  dormant deterministic research matrix
└── cli.py                    operator/developer CLI

web/                          React/Vite launch client; consumes presentation DTOs
tests/                        executable specs, mutation tests, fixture-backed integration tests
```

---

## 3. Runtime data flow

```
Ticker
  │
  ├─► EDGAR submissions ─► filings table ─► newest supported filing (optionally by form)
  │                                           │
  │                                           ▼
  │                                download primary SEC document
  │                                           │
  │                                           ▼
  │                                P0 canonical section text
  │                                + section-relative offsets
  │                                + full source hashes
  │                                           │
  │                                           ▼
  └─► SEC companyfacts ─► as-of FactStore ─► six metrics + decimals/direction slack
                                              │
canonical section catalog + MetricBundle ─────┘
  │
  ├─► precompute current/prior changed ranges (independent of model tool order)
  │
  └─► Generator JSON actions ─► allowlisted deterministic tools
        ─► draft ─► compiler ─► one shared repair ─► finance Skeptic
        ─► final compiler/per-finding prune ─► atomic linked P1 + harness.v2 trace

surviving P1 + exact metric snapshot + stored section text
  │
  ├─► V1 numeric provenance
  ├─► V4 citation exactness
  ├─► V5 strict schema/advice/hygiene
  └─► V2 accounting identities (separate warnings only)
          │
          ▼
atomic verification rows + frozen final trace + filing terminal status
          │
          ▼
projection publication gate (verified + V1/V4/V5 pass + no blocking failure)
          │
          ▼
canonical.build_filing_entry()
  ├─ trusted SEC URL/identity
  ├─ exact offset/quote + actual section SHA-256
  ├─ qualitative headline hygiene
  └─ verify.presentation over the final DTO
          │
          ▼
PresentationService BriefView/FilingDetailView
          ├─► FastAPI JSON ─► React
          ├─► render_brief_markdown() ─► Markdown
          └─► owner-scoped frozen certificate.v2
```

No user-visible content path reads P2, P3, signal logs, dormant claim graphs, extended metrics, or
price/portfolio data. The browser and Markdown are two serializers/surfaces over one canonical
projection, not two independent trust paths.

---

## 4. Production control flow

`pipeline/progress.py` defines the current ledger exactly:

```
download → parse → metrics → extract → verify
```

`pipeline/run.newest_filing_to_analyze()` filters to 10-K/10-Q/8-K, optionally narrows to one form
family, selects one newest filing, and returns no work when that filing is already `verified` or
terminally `analyzed`. It never falls through to older history within the selected scope. A failed
filing is eligible for at most two persisted attempts, calculated across all canonical stages
beginning with `download`; missing URLs and fetch failures consume attempts, preventing one broken
newest issuer from starving the portfolio.

`process_filing()` always passes `resume=False`: every retry downloads and rebuilds the whole attempt.
There is no production API/CLI for accession selection, historical replay, partial-stage rerun, or
offline reverify. The internal orchestrator retains some reusable-artifact support for tests/history;
the launch runner does not expose it.

`llm/harness.py` gives one extraction-stage invocation eight Generator turns, six Generator tool
requests, one preflight, one shared repair, and two Skeptic tool requests. Generator and Skeptic
use independent attempt-wide counters; duplicate calls consume budget but reuse cached results,
and advertised budgets never become negative. A denied seventh Generator tool receives at most one
submit-only nudge and is not executed or traced. Only trusted catalogs enter the initial prompt;
filing text is retrieved as bounded tool data. Changed ranges are precomputed before the first
model action, so `get_changes` only controls model visibility. Once a compiler-passing baseline
exists, optional Skeptic/repair action or budget breakdown restores it and applies only validated
finding-local objections; provider failure remains fail-closed. Each model action is strict JSON
and output is capped at 2,000 tokens. These bounds are distinct from the two persisted full-attempt
bound above.

---

## 5. Fixed launch interface contracts

1. **P0 section contract** — `filing_sections` stores a canonical `section_key`, the section text,
   section-relative provenance metadata, and `text_sha256`. P1 offsets refer to the section text,
   not the original HTML byte stream.

2. **P1 contract** — `llm.schemas.P1Output` rejects extra fields and contains at most three
   uniquely identified findings (`f1`–`f3`). Each finding has one to three exact evidence spans,
   controlled severity/critical flag, and an optional paired registry `metric_id` + direction.
   Trusted filing identity must match, and
   routed Items 1.03/2.04/3.01/4.02 require their critical section-backed finding. No parallel
   general claim graph is needed for launch.

3. **Metric contract** — `metrics.service.MetricsService.compute()` calls only
   `formulas.compute_starter(store, sector, as_of=...)`. The six names come from
   `metrics/catalog.py`. Each persisted row must round-trip the metric name, status, formula
   version, and `as_of`; presentation rejects inconsistent rows. Display vocabulary is neutral:
   share count increased/decreased/flat, and net debt / (operating income + D&A) is explicitly a
   proxy rather than reported EBITDA. Normalized facts and inputs preserve SEC `decimals`.
   Directional metrics expose current-minus-prior delta and the sum of both raw rounding slacks;
   subtraction and slack addition use decimal arithmetic before conversion to the existing float
   DTO. Unknown, overflowing, or float-underflowing finite decimals mean unknown direction, never
   zero slack. User-facing surfaces say “computed as of.” Presentation projects the persisted,
   re-validated formula expression and XBRL inputs into an expandable derivation; a provenance-
   withheld row exposes no derivation.

4. **Point-in-time contract** — companyfacts entries without a provable filing date or filed after
   the filing's `as_of` are excluded before normalization. Current annual legs older than 550 days
   and instant/share/optional-quarterly legs older than 200 days become unavailable; future or
   malformed source dates also fail closed. Repository historical metric selection orders by
   greatest eligible `as_of`, then greatest row ID for a same-date rerun.

5. **Publication-check contract** — the launch `VerifyBundle` contains a required, explicit
   authored-headline subset, exact evidence snippets, starter metrics, stored section text, a null
   trade action, and the disclaimer. The compiler, V5, and final DTO verifier consume one shared
   authored-text policy; exact SEC quotations are never scanned as authored prose. V1/V4/V5 are
   blocking publication checks. V2 is a separate non-blocking source-data audit. V3 is
   `skipped_not_applicable` because no decision exists.

6. **Persisted gate contract** — each P1 is atomically paired with one strict `harness.v2` trace
   containing its analysis ID and exact serialized-output hash. Verification rows, the frozen final
   trace snapshot, filing status, and processed time finalize in one transaction. Presentation loads
   the latest valid finalized v2 trace first and may deserialize only its linked P1 when filing
   status and frozen/live integrity gates agree. Pending, malformed, mismatched, unlinked, or v1
   traces fail closed.

7. **Compiler/prune contract** — `verify.compiler.compile_draft()` classifies errors as run-level or
   finding-local. After one repair it drops only locally invalid findings, recomputes severity, and
   publishes metrics-only when none survive. `FORM_SCOPE`, `CRITICAL_COVERAGE`, provider failure,
   and repeated malformed actions are the only whole-run withholding paths.

8. **Exact DTO contract** — `canonical.build_filing_entry()` is the sole constructor for a public
   filing entry. `verify.presentation.verify_filing_entry()` checks the final object, not a precursor.
   Candidate-local corruption is pruned; identity/persisted-gate/final-DTO corruption withholds.

9. **Rendering contract** — `PresentationService` is the only database-to-content adapter.
   `render_brief_markdown(BriefView)` serializes the same DTO returned to the browser and escapes
   filing/model text as text.

10. **Production model contract** — `FINWATCH_MODEL` and optional `FINWATCH_SKEPTIC_MODEL` must use
   the same supported `openai/` or `openrouter/` provider prefix
   and production credential discovery recognizes `OPENAI_API_KEY` / `OPENROUTER_API_KEY` plus the
   process-memory browser key. Model bake-off flexibility is developer tooling, not runtime provider routing.

11. **Certificate contract** — completed `verified` and completed-withheld `analyzed` attempts may
   expose `certificate.v2`; failed or pending attempts may not. Every hashed field comes from the
   finalized frozen trace, including filing identity/source hash, linked P1/trace IDs and P1 hash,
   safe verification rows, publishable evidence/classification, metric envelopes, tool hashes,
   agenda, budgets, models, prompts, outcome, and terminal reason. Withheld snapshots are redacted
   before persistence: no published IDs, classification, evidence, headline/quote prose,
   verification details, or tool arguments. Canonical UTF-8 JSON uses sorted keys, compact
   separators, and `ensure_ascii=False`, so later row mutations cannot change certificate bytes.

12. **Ticker-only contract** — public holding create/update schemas accept identity only. Shares,
    cost basis, targets, horizons, and theses are neither collected nor returned. Dormant DB columns
    do not expand the public contract.

---

## 6. Publication invariants

| Invariant | Enforcement |
|---|---|
| Maximum three findings | P1 schema, prompt, canonical DTO, final DTO verifier |
| Every finding has direct evidence | P1 schema + canonical exact-span construction |
| Headline contains no number | compiler + V1/authored-text path + final DTO verifier |
| Quote is exact | V4 + canonical offset equality + full-hash DTO verification |
| Citation points to SEC | Canonical trusted URL construction + final DTO verifier |
| Metric number is deterministic | starter formula → persisted computation → validated metric DTO |
| Structured direction exceeds rounding uncertainty | SEC decimals → metric slack → compiler |
| Future fact cannot enter filing-dated computation | `metrics.service.as_of_facts()` |
| Local failure leaks no bad finding | compiler/canonical per-finding prune + dropped codes |
| Run-level failure leaks no LLM bytes | persisted projection gate + withheld DTO |
| Browser and Markdown agree | both consume the same `BriefView` |
| Trade/price language is absent | P1 prompt/schema, V5, final DTO verifier |
| Uncertainty is explicit | universal metric states (plus a presentation-only `withheld` row state) + reviewed/withheld filing presentation states |
| Gate outcome is visible to the user | filing-detail verification band over persisted V1/V4/V5 + V2 rows |

Verification establishes these mechanical invariants. It cannot establish whether the LLM chose the
three most economically important facts; evaluation and user feedback measure that product question.

---

## 7. SQLite and execution model

The deployment model is one process/container, one SQLite file, and one in-process job worker.

- `create_app()` synchronously installs or verifies the exact file-backed schema once. Requests and
  jobs call `connect()`, never schema installation. Demo databases are separate.
- Operational connections enable foreign keys, WAL, and a 5-second busy timeout.
- Schema v6 has `users`, private `user_companies`, and private `user_preferences`. A reserved local
  user preserves auth-free CLI/local behavior. Issuer identity, filings, facts, analyses, metrics,
  and verification remain shared public-data artifacts.
- XBRL replacement, filing-section/FTS replacement, computation batches, and verification-report
  replacement are atomic and roll back on failure. P1/trace insertion and final attempt
  verification/status publication are also transactional.
- POSIX DB files are `0600`; newly created data directories are `0700`.
- Jobs are owner-tagged process-memory state and disappear on restart. There is no durable queue,
  leasing, distributed worker, or multi-instance consistency claim.

There is no migration ladder. Schema v6 rejects an older database with a backup-and-reset message;
v1 traces and certificates are not adapted, and Git history is the compatibility archive.

---

## 8. Web trust boundary

Loopback is the default. Non-loopback serving requires explicit `--allow-remote`; remote app
construction additionally requires a ≥32-character signing secret, Resend sender configuration,
an operator `SEC_USER_AGENT`, and an explicit host allowlist. Wildcard hosts are invalid. Public
signup accepts any valid email, issues a six-digit code with a ten-minute lifetime and five-attempt
limit, and creates a private workspace after successful verification. Challenges and rate limits are
process memory only.

The login is a signed 30-day `HttpOnly`, `Secure`, `SameSite=Lax` cookie containing only opaque IDs
and expiry. A separately signed double-submit token protects cookie-authenticated mutations; exact
Origin checks remain mandatory. Local mode is auth-free and CORS is limited to local development
origins. API responses are no-store and carry CSP/frame/nosniff/referrer headers; remote mode adds
HSTS. The ASGI body limiter rejects declared and streamed bodies over 1 MiB. Decoded EDGAR responses
stop at 64 MiB before cache writes. Job responses discard exception/provider strings and diagnostics
and expose only fixed safe messages; unhandled request errors use a generic JSON contract.

Sessions are stateless: logout deletes the browser cookie and its in-memory provider key, while a
copied cookie remains valid until expiry or signing-secret rotation. This is the explicit consequence
of keeping a persistent session registry out of the prototype.

Server-side ownership checks scope watchlists, briefs, filing/metric access, preferences, actions,
and job polling. Cross-user resource requests return 404. Hosted ticker registration is serialized
and capped at 25 tracked tickers per workspace; the instance retains one global worker.

Hosted participant API keys exist only in `RuntimeSecrets`, keyed by opaque session ID and captured
before a job is enqueued. They are never placed in SQLite, cookies, browser storage, job DTOs, logs,
or API responses; expired-session entries are pruned from memory. Hosted requests ignore provider
keys from the environment; environment discovery remains for CLI/local mode. SQLite stores account
emails, private ticker membership/preferences, an optional local operator SEC User-Agent, and public
filing artifacts in plaintext; filesystem/container access is the data-at-rest boundary.

---

## 9. Evaluation contract

`src/finwatch/evals/golden_set/manifest.yaml` pins 12 real SEC accessions spanning critical, boring,
and routine cases. The recorded harness runs P0/metrics/tool harness/verification/canonical projection without network
or keys. The optional live harness fetches the pinned primary document and calls a candidate model.

Acceptance emphasizes 100% critical-flag recall, valid strict JSON, publication/canonical pass rate,
and no screaming false alarms on boring filings. Tokens and cost are measured. The bake-off CLI may
compare models for development; production still runs one configured OpenAI-backed model.

---

## 10. Dormant research boundary

The following are intentionally disconnected from `build_orchestrator()`, FastAPI routes, browser
types/pages, digest rendering, and launch CLI controls:

- `P2Explainer`, P2 prompt/schema, portfolio/cross-holding/thesis analysis;
- P3 prompt/schema/rationale, `signals/matrix.py`, adapters, shadow logging, track-record UI;
- extended metrics, Stooq/price paths, valuation/position/rebalance inputs;
- historical analysis replay, offline reverify, partial-stage user reruns;
- legacy claim-graph, portfolio fields, shadow tables, and FTS search as product features.

Keep dormant code clearly labeled and isolated. Reactivation requires an explicit plan that updates
this document, the mirrored context files, threat model, user contract, and tests in the same change.

The one pinned post-harness addition is a bounded `resolve_fact` tool for registered metrics with a
missing precondition: Tier 0 aliases/derivations, then current-filing Tier 1 exact-span extraction,
with at most two facts and three searches. Admission requires deterministic parse, explicit
unit/scale/period, input-contract compatibility, and redundant-derivation agreement within combined
rounding slack. Tier 2+ sources, generic plugins, subagents, bitemporal redesign, financial mathlib,
Lean/Z3/SMT, and discovery/alpha systems remain deferred.

---

## 11. Why the trust modules need extra care

Their shared risk is silent authority: a tolerant matcher, wrong period selection, formula edge case,
or provenance seam can produce a plausible answer carrying a verified label. Pure deterministic
logic and mutation tests are the defense. The launch cut reduces that surface by making fewer claims,
but it does not lower the standard for those claims.

Everything outside Tier 1 should still fail safely, especially at boundaries: untrusted SEC text into
P1, P1 into verification, persisted rows into the canonical DTO, and the DTO into browser/Markdown.
