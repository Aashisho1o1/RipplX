# to_do — deferred work from the launch review

Findings from the pre-launch adversarial review (2026-07-24) that are **not urgent**. Phase 0 (the
user-visible trust and doc fixes) and the safe deletions are already done and committed.

Review scope: 8 dimensions, 45 raw findings, every S0–S2 claim independently re-verified by a second
agent instructed to refute it. **No S0 or S1 defects were found.** The core promise — add a ticker,
get ≤3 evidence-anchored changes plus six deterministic metrics from the newest filing — is
implemented end to end and verified live against real SEC filings.

Severity: S2 high · S3 medium · S4 low. Confidence noted where it is not High.

---

## Phase 0.5 — the sample's remaining gap (do before promoting the link widely)

### S2 · Sample evidence is not byte-authentic to the documents it links
The bundled filings are abridged excerpts (a few hundred bytes to ~2 KB) while the UI links
"Open SEC filing" to the complete document. The displayed character offsets and section SHA-256
therefore attest to the excerpt and will not reproduce against EDGAR. For a product whose entire
claim is verifiable provenance, this is the worst place to overclaim.

**Interim (shipped f54e746):** both sample surfaces now state plainly that the copies are abridged
excerpts and that the offsets/hash prove the excerpt, not the full document.

**Real fix:** bundle complete frozen primary documents for the demo cases and re-record their P1
outputs, so a visitor can verify a quote against EDGAR character-for-character. Cost is repo size
(a real 10-Q is ~500 KB–3 MB each); consider one byte-authentic hero filing plus abridged others,
clearly distinguished. Related: `src/finwatch/evals/golden_set/manifest.yaml` calls its HTML
"representative fixtures", so the same caveat applies to the golden set.

### S3 · The sample proves verification wiring, not discovery
`DemoLLM` replays a canned submit, so every sample trace reports zero tool calls, zero repairs, and
zero dropped findings. Either replay an authentic recorded action sequence (search → get_metric →
submit → skeptic) or state in the UI that the sample is a verification replay.

---

## Phase 1 — trust integrity (do before wider use)

### S2 · Rounding-aware metric direction is inert in production — HALF-ADDRESSED
**Update 2026-07-24:** the P1 prompt no longer induces the annotation (it now instructs
`metric_id`/`direction` to be null), which stopped it destroying findings — it had killed 3 of
4 findings in a live GOOGL sample. The dead code path remains: `llm/schemas.py` still accepts the
fields, `verify/compiler.py:111-118` still branches on them, and `direction_delta`/
`direction_slack`/`direction_basis` + `xbrl_rounding_slack` are still computed and persisted.
Decide (a) delete them, or (b) give slack a real source, then update AGENTS.md §8/§9 — which still
documents this as shipped behaviour. Original analysis below.


Real SEC companyfacts carries **no `decimals` key** — 108,895 fact entries scanned across three
issuers, zero present. So `direction_slack` is always `None`, `deterministic_direction` returns
`None`, and every finding that follows the prompt's own `metric_id`+`direction` instruction is pruned
as `METRIC_DIRECTION_UNAVAILABLE`. Worse, the failure consumes bounded budget (a `check_draft`
preflight or the single shared repair), after which a later Skeptic objection can no longer be
repaired. Tests pass only because fixtures inject `decimals` that production never has.

**This is a product decision, not a bug fix.** Either:
- (a) delete the mechanism: `metric_id`/`direction` from `prompts/P1_extractor.md` and
  `llm/schemas.py`, the branch in `verify/compiler.py:111-118`, and `direction_delta`/
  `direction_slack`/`direction_basis` + `xbrl_rounding_slack` if nothing else consumes them; or
- (b) give slack a real source by parsing `decimals` from the filing's own XBRL instance.

Either way `AGENTS.md` §8/§9 must be updated to match. Proving test: an integration test over a real
cached companyfacts asserting `deterministic_direction is not None` — it fails today.

### S2 · A renumbering repair erases a Skeptic drop from the certificate
`llm/harness.py:1173` and `:1189` test survival by the model-chosen `finding_id`, while
`surviving_sigs` (signature-keyed, `:1163`) is computed and used only for carry-over. If a repair
drops the objected finding and renumbers a clean one onto its id, the drop vanishes:
`dropped_findings: []`, outcome `published`, terminal `verified`. That propagates into the
owner-visible `certificate.v2`. The `_finding_signature` docstring itself warns about exactly this.
**Fix:** make both survival tests signature-aware, mirroring `:1163`.

### S2 · Sign-in rate limits are recorded only after a successful send
`web/auth.py:257-287` checks limits, sends, then appends counters — so any provider exception skips
the counters entirely. Verified against the real remote app: 300 POSTs for one address → 300 outbound
attempts, zero 429s; the 100/hour global ceiling never engaged. Because the send happens while
holding the lock with a 10 s timeout, legitimate sign-ins serialize behind it.
**Fix:** reserve budget *before* dispatching and keep the reservation when delivery fails; store the
challenge only on success.

### S2 · A twice-failed filing reports "already been analyzed"
`pipeline/run.py:102-115` returns `None` for two different causes (terminal vs retry-exhausted) and
`web/app.py:961-968` collapses them into the unconditional `newest_already_analyzed`. The brief
simultaneously buckets the same accession as `pipeline_failed`. The issuer then goes dark with no
signal at the point of action.
**Fix:** add a fourth typed reason (`newest_attempts_exhausted`) to `web/jobs.py` and select it when
the newest supported filing is `failed`/attempt-exhausted.

### S3 · V2 severity has two contradictory sources of truth
`verify/checks.py:275-344` marks V2a/V2b/V2c `severity="blocking"` while the orchestrator downgrades
them. V2 must be non-blocking everywhere.
**Fix:** emit `warning`/`info` inside `check_v2_identities`; delete the unused `store`/`sector`
params from `run_all`.

### S3 · Skeptic loop still uses the cumulative invalid-action counter
`llm/harness.py:790` — the generator loop was fixed to reset on success; the Skeptic was not, so two
non-consecutive malformed replies abandon the whole finance-review pass.
**Caution:** the Skeptic re-increments after a *valid* action on `UNKNOWN_FINDING_ID`, so a naive
reset risks a loop. Verify the turn cap bounds it before changing.

### S3 · `upsert_filing` is `INSERT OR IGNORE`
`db/repositories.py:281-296` — a filing indexed without `primary_doc_url` can never be repaired by
re-syncing, permanently blocking that issuer.
**Fix:** `ON CONFLICT(accession_number) DO UPDATE SET primary_doc_url = COALESCE(excluded.primary_doc_url, filings.primary_doc_url)` — fill a missing URL only, never overwrite.

---

## Phase 2 — coverage gaps (real, bounded)

- **S3 · Browser pipeline path has zero tests.** `web/app.py:827-873` (`sync_work`) and `:884-1027`
  (`analysis_work`), the no-op reasons, stage-progress projection, and `JobRegistry.add_item` are
  entirely untested. Add tests driving the real work function with a fake fetcher + `FakeLLMClient`.
- **S3 · `AMBIGUOUS_QUOTE` has zero coverage** and survives full deletion — it is the rule that makes
  server-derived offsets trustworthy. Add one compiler test with a snippet occurring twice.
- **S3 · Four V4/V5 branches are unreachable in production** (`verify/checks.py:361-370`, `:400-409`)
  and unkilled by the mutation battery. Either make them live (populate `EvidenceClaim.text_sha256`
  in `assemble_verify_bundle`) or delete them — do not leave them as decorative.
- **8-K critical-item detection is unverified against live HTML.** If `split_8k` never emits an Item
  1.03/2.04/3.01/4.02 key, `_critical_coverage` requires nothing. Needs a live-fixture test.

---

## Phase 3 — deletion and consolidation (safe, non-urgent)

Already done: P3 vocabulary in `core/types.py`, all 8 `#AS:` notes, the dead P3 fixture, dead CSS,
`FRONTEND_PLAN.md` + `DEFERRED_ISSUES.md` (306 KB), the empty `signals/` directory, and the
SYSTEM_DESIGN module map.

Remaining, each needing its own care:

- **Schema-touching (batch into ONE version bump with backup/reset — there is no migration ladder):**
  - `section_fts` FTS5 index is **write-only** — every filing section's text is stored twice and no
    production code queries it (`db/schema.sql:60`, `repositories.py:381-424`).
  - `computations` has no index; `latest_computations` runs an O(n²) correlated scan per tracked
    company on the watchlist and brief. Add
    `CREATE INDEX ix_computations_ticker_tool_asof ON computations(ticker, tool, as_of DESC, id DESC)`.
- **`Repo.insert_verification_results`** (`repositories.py:644-657`) — a second unguarded
  auto-committing write path with no production caller; three test call sites must move to
  `finalize_p1_attempt` first.
- **Orchestrator resume/reuse machinery** (`pipeline/orchestrator.py:223-361`) — unreachable from
  every shipped surface, zero tests, and its extract-reuse branch would silently re-finalize an
  already-published attempt.
- **Disclaimer has three sources of truth** — `core/types.py:8` plus two hardcoded frontend copies
  (`CompanyPage.tsx:13`, `SetupPage.tsx:6`). Project it through the DTO instead.
- **Stage-failure labels duplicated** verbatim in `pipeline/progress.py:37-47` and
  `ProvenancePanel.tsx:30-40` with no guard test (the sibling drop-code table has one).
- **Same-provider check** exists in `web/app.py:913-915` but not in `config.py`, so the CLI path is
  unguarded. Move it to a `Config` validator.
- Vestigial: `MetricResult.zone_or_flag`, `MetricsBundle.valuations`/`computed`,
  `FactStore._facts_for`, `formulas._val`, `ResolvedSettings.api_key_source`,
  `StageReporter.skipped`, `VerifyBundle.trade_action`.

---

## Phase 4 — polish

- **S3 · "All checks passed" is shown while four checks were skipped** (`VerificationBand.tsx:16-20`).
  Reword to "Publication gate passed" or render a ran/total split.
- **S3 · A never-analyzed filing is called "a legitimate routine result"** (`FilingPage.tsx:96`).
  Add an explicit `not_analyzed` branch.
- **S3 · Analysis panel says "across all tracked companies"** for a run that analyzes exactly one
  filing (`AnalysisPanel.tsx:37,45`). Name the unit of work.
- **S3 · Demo brief renders 22 of 24 metric rows as unavailable** (`demo/demo.py:85-89,123-127`) —
  contradicts the "six verified financial deltas" promise on the public onboarding surface. Align the
  demo MSFT `filed`/`as_of` with the bundled fixture vintage, or drop fact-less issuers.
- **S3 · TTM revenue and four-quarter direction are structurally uncomputable** for real issuers, so
  the table permanently prints "(TTM revenue n/a)" and leaks the internal token
  `insufficient_points` (`metrics/formulas.py:182-276`, `presentation/formatting.py`).
- **S3 · `foundation.md` changed without a `PROMPT_SUITE_VERSION` bump**, so one certified
  `prompt_version` maps to two different injection-defense texts. Bump and pin with a sha256 test.
- **S4 · 500 responses bypass the security-header middleware** (`web/app.py:404-419` vs `:445-456`).
- **S4 · Circular import**: `finwatch.verify.presentation` cannot be imported first.
- **S4 · `analysis_work` leaks the SQLite connection and EdgarClient** when the skeptic-provider
  guard fires before the `try:` block (`web/app.py:896,911,913-915,929`).

---

## Open product questions (moved out of shipped source)

These were `#AS:` notes committed in trust-critical files; the code is now clean and the questions
live here.

1. **Is `FORBIDDEN_VOCABULARY` worth keeping?** The current list ("guaranteed", "moon", "obvious",
   "no-brainer", …) is small enough that it may not have decisive regulatory value, and a substring
   list is a blunt instrument. Either justify it explicitly or replace it with something better —
   note that any replacement must stay deterministic; it is a publication gate, not a suggestion.
2. **Is `CRITICAL_DOC_FLAGS` exhaustive enough?** Hand-written red-flag codes will miss cases. A
   cheap LLM call was suggested — but critical coverage is currently *deterministic and fail-closed*,
   and making it model-dependent would move a launch-blocking gate into stochastic territory. If
   explored, the model may only *widen* coverage, never shrink it.
3. **Sector classification** (`SectorClass`, `sector_from_sic`) is still computed but nothing in the
   lean frontend surfaces it. It is consumed by `metrics/service.build_sector`; confirm whether the
   metrics layer still needs it or whether it can go.

---

## Not defects (recorded so they are not re-litigated)

Verified during review and deliberately accepted: stateless sessions with no central revocation,
ephemeral in-process jobs, plaintext-at-rest SQLite behind filesystem permissions, single worker,
newest-only processing. All are documented limitations, and the shipped surfaces do not misrepresent
them.
