# finwatch — SYSTEM_DESIGN.md
## Launch architecture, module map, and integration contracts

This document maps the narrow launch runtime. It sits below the code/tests and the mirrored
`AGENTS.md`/`CLAUDE.md` context files. `CORE_CODE.md` and
`docs/CLAUDE_v0.2_full_spec.md` describe the broader historical research system.

> **Precedence:** shipped code + tests > `AGENTS.md`/`CLAUDE.md` > this map > historical specs.

The repository still contains P2/P3, signals, extended formulas, and legacy tables. Their presence
does not make them production stages. The runtime wiring in `pipeline/run.py` is authoritative.

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
│   ├── schemas.py            strict launch P1 contract + dormant P2/P3 schemas
│   ├── stages.py             bounded P1 call/schema repair/persistence
│   └── router.py             injected client; LiteLLM implementation
├── prompts/
│   ├── foundation.md         prompt-injection and evidence rules
│   ├── P1_extractor.md       launch max-three-finding prompt
│   └── P2_impact.md, P3_rationale.md                 dormant research
├── xbrl/                     point-in-time SEC companyfacts normalization
├── metrics/
│   ├── catalog.py            six launch metrics and labels
│   ├── service.py            launch-only compute/persist orchestration
│   ├── envelope.py           universal result/provenance envelope        ⚙
│   └── formulas.py           starter + dormant extended pure formulas    ⚙
├── pipeline/
│   ├── run.py                newest-only production wiring/scheduling
│   ├── orchestrator.py       P0→P1→starter metrics→publication gate
│   ├── progress.py           five-stage persisted ledger
│   └── adapters.py           dormant P2/P3/matrix adapters
├── verify/
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
│   ├── jobs.py               one-worker safe public job registry
│   ├── runtime.py            non-secret settings + memory-only key
│   └── security.py           remote bearer/host policy
├── db/                       SQLite schema, ordered migrations, repositories
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
  ├─► EDGAR submissions ─► filings table ─► newest supported filing only
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
  │                                P1: 0..3 findings
  │                                + 1..3 exact quotes each
  │
  └─► SEC companyfacts ─► as-of FactStore ─► compute_starter() ─► computations

P1 + starter MetricBundle + stored section text
  │
  ├─► V1 numeric provenance
  ├─► V4 citation exactness
  ├─► V5 strict schema/advice/hygiene
  └─► V2 accounting identities (separate warnings only)
          │
          ▼
persisted verification rows + filing terminal status
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
          └─► render_brief_markdown() ─► Markdown
```

No user-visible content path reads P2, P3, signal logs, dormant claim graphs, extended metrics, or
price/portfolio data. The browser and Markdown are two serializers/surfaces over one canonical
projection, not two independent trust paths.

---

## 4. Production control flow

`pipeline/progress.py` defines the current ledger exactly:

```
download → parse → extract → metrics → verify
```

`pipeline/run.newest_filing_to_analyze()` filters to 10-K/10-Q/8-K, selects one newest filing, and
returns no work when that filing is already `verified` or terminally `analyzed`. It never falls
through to older history. A failed filing is eligible for at most two persisted extraction-stage
attempts.

`process_filing()` always passes `resume=False`: every retry downloads and rebuilds the whole attempt.
There is no production API/CLI for accession selection, historical replay, partial-stage rerun, or
offline reverify. The internal orchestrator retains some reusable-artifact support for tests/history;
the launch runner does not expose it.

`llm/stages.py` gives one extraction-stage invocation one initial model call plus at most one strict
schema-repair call. Input is capped at 240,000 serialized characters; output is capped at 2,000
tokens. This model-call bound is distinct from the two persisted full-attempt bound above.

---

## 5. Fixed launch interface contracts

1. **P0 section contract** — `filing_sections` stores a canonical `section_key`, the section text,
   section-relative provenance metadata, and `text_sha256`. P1 offsets refer to the section text,
   not the original HTML byte stream.

2. **P1 contract** — `llm.schemas.P1Output` rejects extra fields and contains at most three
   qualitative, non-numeric findings. Each finding has one to three exact evidence spans, controlled
   severity, and an optional controlled critical flag. Trusted filing identity must match, and
   routed Items 1.03/2.04/3.01/4.02 require their critical section-backed finding. No parallel
   general claim graph is needed for launch.

3. **Metric contract** — `metrics.service.MetricsService.compute()` calls only
   `formulas.compute_starter(store, sector, as_of=...)`. The six names come from
   `metrics/catalog.py`. Each persisted row must round-trip the metric name, status, formula
   version, and `as_of`; presentation rejects inconsistent rows. Display vocabulary is neutral:
   share count increased/decreased/flat, and net debt / (operating income + D&A) is explicitly a
   proxy rather than reported EBITDA. User-facing surfaces say “computed as of.”

4. **Point-in-time contract** — companyfacts entries without a provable filing date or filed after
   the filing's `as_of` are excluded before normalization. Current annual legs older than 550 days
   and instant/share/optional-quarterly legs older than 200 days become unavailable; future or
   malformed source dates also fail closed. Repository historical metric selection orders by
   greatest eligible `as_of`, then greatest row ID for a same-date rerun.

5. **Publication-check contract** — the launch `VerifyBundle` contains P1 qualitative headlines,
   exact evidence snippets, starter metrics, stored section text, a null trade action, and the
   disclaimer. V1/V4/V5 are blocking publication checks. V2 is a separate non-blocking source-data
   audit. V3 is `skipped_not_applicable` because no decision exists.

6. **Persisted gate contract** — `presentation.projection.load_filing_projection()` may deserialize
   P1 only when filing status is `verified`, persisted V1/V4/V5 rows all pass, and no blocking result
   fails. Otherwise P1 is absent and the filing is marked withheld/manual review.

7. **Exact DTO contract** — `canonical.build_filing_entry()` is the sole constructor for a public
   filing entry. `verify.presentation.verify_filing_entry()` checks the final object, not a precursor.
   One candidate error withholds the complete filing analysis; it never silently drops a bad finding
   and publishes the remainder as clean.

8. **Rendering contract** — `PresentationService` is the only database-to-content adapter.
   `render_brief_markdown(BriefView)` serializes the same DTO returned to the browser and escapes
   filing/model text as text.

9. **Production model contract** — `FINWATCH_MODEL` must have an `openai/` prefix and production
   credential discovery recognizes only `OPENAI_API_KEY` plus the process-memory browser key.
   Model bake-off flexibility is developer tooling, not runtime provider routing.

10. **Ticker-only contract** — public holding create/update schemas accept identity only. Shares,
    cost basis, targets, horizons, and theses are neither collected nor returned. Dormant DB columns
    do not expand the public contract.

---

## 6. Publication invariants

| Invariant | Enforcement |
|---|---|
| Maximum three findings | P1 schema, prompt, canonical DTO, final DTO verifier |
| Every finding has direct evidence | P1 schema + canonical exact-span construction |
| Headline contains no number | P1 schema + V1/authored-text path + final DTO verifier |
| Quote is exact | V4 + canonical offset equality + full-hash DTO verification |
| Citation points to SEC | Canonical trusted URL construction + final DTO verifier |
| Metric number is deterministic | starter formula → persisted computation → validated metric DTO |
| Future fact cannot enter filing-dated computation | `metrics.service.as_of_facts()` |
| Failed verification leaks no LLM bytes | persisted projection gate + withheld DTO |
| Browser and Markdown agree | both consume the same `BriefView` |
| Trade/price language is absent | P1 prompt/schema, V5, final DTO verifier |
| Uncertainty is explicit | universal metric states + boring/withheld presentation states |

Verification establishes these mechanical invariants. It cannot establish whether the LLM chose the
three most economically important facts; evaluation and user feedback measure that product question.

---

## 7. SQLite and execution model

The deployment model is one process/container, one SQLite file, and one in-process job worker.

- `create_app()` synchronously initializes/migrates the file-backed production DB once. Requests and
  jobs call `connect()`, never the migration runner. Demo databases are separate.
- Operational connections enable foreign keys, WAL, and a 5-second busy timeout.
- Migrations are ordered via `PRAGMA user_version`; duplicate holdings fail migration closed before
  the unique CIK index is applied.
- XBRL replacement, filing-section/FTS replacement, computation batches, and verification-report
  replacement are atomic and roll back on failure.
- POSIX DB files are `0600`; newly created data directories are `0700`.
- Jobs are process-memory state and disappear on restart. There is no durable queue, leasing,
  distributed worker, or multi-instance consistency claim.

The DB retains dormant v0.2 tables/columns for compatibility. A legacy table's existence is not
authorization for new production writes.

---

## 8. Web trust boundary

Loopback is the default. Non-loopback serving requires explicit `--allow-remote`; remote app
construction additionally requires a ≥32-character bearer token and explicit host allowlist.
Wildcard hosts are invalid. The bearer is an operator/admin credential, kept only in JavaScript
module memory and lost on refresh; it is not a participant login or tenant boundary. Participants
must not share an instance: use operator-mediated sessions or one isolated DB/container/token per
participant.

Local mutations require an allowed Origin. Remote API access requires the bearer token. CORS is
limited to local development origins. API responses are no-store and carry CSP/frame/nosniff/referrer
headers; remote mode adds HSTS. The ASGI body limiter rejects declared and streamed bodies over
1 MiB. Decoded EDGAR responses stop at 64 MiB before cache writes. Job responses discard
exception/provider strings and diagnostics and expose only fixed safe messages; unhandled request
errors use a generic JSON contract.

Hosted ticker registration is serialized and capped at 25 tracked tickers per workspace. This is an
alpha resource/wallet bound, not multi-tenant isolation or participant authorization.

API keys exist only in the environment or `RuntimeSecrets` process memory. Settings responses expose
configuration state, never key material. SQLite stores the SEC User-Agent and tracked ticker data in
plaintext; filesystem/container access is the data-at-rest boundary.

---

## 9. Evaluation contract

`src/finwatch/evals/golden_set/manifest.yaml` pins 12 real SEC accessions spanning critical, boring,
and routine cases. The recorded harness runs P0/P1/verification/canonical projection without network
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

---

## 11. Why the trust modules need extra care

Their shared risk is silent authority: a tolerant matcher, wrong period selection, formula edge case,
or provenance seam can produce a plausible answer carrying a verified label. Pure deterministic
logic and mutation tests are the defense. The launch cut reduces that surface by making fewer claims,
but it does not lower the standard for those claims.

Everything outside Tier 1 should still fail safely, especially at boundaries: untrusted SEC text into
P1, P1 into verification, persisted rows into the canonical DTO, and the DTO into browser/Markdown.
