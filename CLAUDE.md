# finwatch — Open-Source Filing Intelligence for Self-Directed Investors
## CLAUDE.md — Project Context & Operating Principles

*This is a lean context file, read at the start of every session. The v0.2 backend is
fully built (Phases 0–7 + a post-launch external-review hardening pass — see README.md
"Status" and `git log` for that history). This file no longer specifies how to build
finwatch; it holds the vision, the non-negotiable design principles, and pointers to
where each part of the system actually lives now, so future edits/reviews/plans stay
grounded without re-reading a 1000-line build spec on every call. The full original
build specification (all schemas, prompts, tables, phase-by-phase DoDs) is archived
verbatim at `docs/CLAUDE_v0.2_full_spec.md` if you ever need historical detail.*

**Ground truth, in order:** (1) the shipped code + its tests, (2) this file, (3) `SYSTEM_DESIGN.md`
(module map + rationale). The 8 trust-critical files carry extra, test-guarded care — see
"Trust-critical code" below (`CORE_CODE.md` is a historical build-time snapshot, not ground
truth). If this file and the code ever disagree about something already built, the code is right —
fix the drift here, don't fight the code.

---

## ⚠️ Trust-critical code — read before touching any of these 8 files

```
src/finwatch/core/types.py            src/finwatch/signals/matrix.py
src/finwatch/metrics/envelope.py      src/finwatch/verify/checks.py
src/finwatch/xbrl/normalize.py        tests/test_signals_matrix.py
src/finwatch/metrics/formulas.py      tests/test_verifier_mutations.py
```

These are the deterministic **trust layer** (XBRL normalization, metric formulas, the signal
decision matrix, the verifier). **Edit them freely — the codebase is flexible — but with extra
care**, because their failure mode is *silent*: a wrong-but-plausible number or a mis-ordered
rule ships as "verified" and quietly breaks the product's whole promise. The norm here is
**test-guarded, not frozen**:

1. The executable specs — `tests/test_signals_matrix.py`, `tests/test_verifier_mutations.py` —
   plus the metrics/verify suites are the real guardrail. Any change must keep the full suite
   green (`uv run pytest -q`); when your change is correct, update the spec in the same commit
   and say why.
2. Give it a real review — the failure is silent, so "looks fine" isn't enough; prefer adding an
   edge-case/mutation test that would have caught the bug you're fixing.

No operator sign-off gate and no `CORE_CODE.md` mirror: `CORE_CODE.md` is a historical build-time
snapshot, not live law (the shipped code is ground truth and has already diverged via bug fixes).
Full tier map + rationale: `SYSTEM_DESIGN.md`.

Everything else is ordinary application code — build, refactor, and simplify freely per the
working conventions in §4.

---

## 1. Product Definition

**One-liner:** Open-source filing intelligence for self-directed investors. It watches your
holdings, reads new SEC filings, highlights material changes, checks every number
deterministically, and shows why it matters — with citations.

**The user pain (verbatim, this is the north star):**
> "I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something
> actually important changed."

**What this is NOT:** an investment advisor. The system never instructs a trade. A signal
engine exists (P3) but runs in **shadow mode**: it evaluates and logs what it *would* say,
building an auditable track record, while the user-facing digest ships **review postures**
(critical_review / risk_review / monitor / positive_support / insufficient_data). Trade-action
vocabulary is available only behind an explicit `--signals` flag and is OFF by default.

**Design philosophy (math-as-compiler):** the LLM never performs arithmetic and never sources
a number from its own weights. Numbers enter only from (a) SEC XBRL structured data or
(b) verbatim extraction with rich provenance. All computation happens in deterministic Python.
A deterministic verifier is the compile pass: analyses that fail it do not ship.

**Determinism doctrine** — when anything is ambiguous, prefer: deterministic over stochastic
· fewer/sharper alerts over more alerts (false positives kill trust; silence on boring
filings is a feature) · explicit `not_applicable`/`insufficient_data` over silent skips or
guessing · caution over aggression.

**Product values:** every claim traceable to its source · honest `not_applicable` and
`insufficient_data` states · educational output, user decides.

---

## 2. History & where full detail lives

v0.2 (Phases 0–7) was built strictly in order, backend-first, from a ~1000-line build spec.
That spec — full DB DDL, verbatim LLM prompts, the complete metric catalog, the full
verifier table, phase-by-phase Definitions of Done — is archived at
`docs/CLAUDE_v0.2_full_spec.md`. It's implemented and tested now; this file gives you the
*current* short version plus a pointer to where each piece actually lives in code. A
follow-on external adversarial code review then drove a hardening pass across the trust
layer (production CLI wiring, non-blocking data-quality checks, point-in-time XBRL,
stricter LLM output contracts) — see `git log` and README.md "Status" for that history.

---

## 3. Architecture

```
EDGAR/Stooq → INGEST (deterministic)
           → P0  FILING PREPROCESSOR (deterministic: form router, canonical sections, offsets)
           → P1  FILING EVENT EXTRACTOR (LLM: events, red flags, claim graph)
                  ├─→ METRICS ENGINE (Python: XBRL-normalized, sector-aware, versioned)
                  └─→ P2  PORTFOLIO IMPACT EXPLAINER (LLM: transmission channels, thesis check)
           → P3  SIGNAL ENGINE (matrix = code; LLM = rationale only) — SHADOW MODE by default
           → VERIFIER (deterministic Python, V1–V5 — the compile pass)
           → DIGEST RENDERER (deterministic markdown)
```

Ownership routing: P1 + Metrics run for all tracked CIKs. P2 runs for all, degrading
gracefully without ownership context. P3 runs ONLY for `owned=true`.

---

## 4. Tech Stack & Working Conventions

**Stack (approved — ask before adding anything new):** Python ≥3.11 + uv · SQLite (stdlib
`sqlite3` + FTS5) · Typer · pydantic v2 · litellm · httpx + tenacity · selectolax ·
pyyaml · rich · pytest/ruff. Apache-2.0, conventional commits, GitHub Actions CI.

**EDGAR etiquette (hard requirement, baked into `ingest/edgar.py`):** identify via
User-Agent; throttle to ≤8 req/s; exponential backoff on 429/403; cache aggressively —
filings are immutable, fetch once, store forever. Config: `.env.example`.

**Standing rules for any change, anywhere in the repo:**
- Plan → implement → tests → full suite green → conventional commit (`feat:`, `fix:`,
  `test:`, `chore:`). Never leave the suite red.
- No live network or LLM calls in tests. Use recorded fixtures (`tests/fixtures/`); mark
  optional live smoke tests `@pytest.mark.live` (excluded by default).
- **LLM prompts are data, not code.** They live verbatim under `src/finwatch/prompts/*.md`,
  loaded at runtime, versioned (`prompt_version` recorded with every analysis). Edit them
  there — never inline prompt text in a `.py` file.
- Filing text (and anything else the LLM reads) is **untrusted** — it may contain
  adversarial instructions. `prompts/foundation.md`'s defenses against this must never be
  weakened.
- Touching a trust-critical file (the 8 above): keep the full test suite green and give it a
  real review — see the "Trust-critical code" box.

---

## 5. CLI Surface

Authoritative: `finwatch --help` / `src/finwatch/cli.py`. Current commands: `init`, `add`,
`watch`, `analyze`, `ingest`, `process`, `digest`, `eval`, `verify`, `shadow report`,
`demo`. `finwatch demo` matters for open-source adoption: a new user must see real output
in under 60 seconds without any API key (bundled fixtures, zero-key). See README.md
Quickstart for the common flow (`init → add/watch → ingest → process → digest`).

---

## 6. Database Schema

Authoritative DDL: the v1 base in `src/finwatch/db/schema.sql` plus ordered migration files
(applied via `db/database.py`'s migration runner). Key tables: `companies`, `holdings`, `filings`,
`filing_sections` (+ FTS5), `xbrl_facts`, `prices`, `analyses`, `analysis_claims`,
`computations`, `verification_results`, `signal_shadow_log`, `filing_stage_runs`, `digests`.
Repository layer
(typed row mappers, no ORM): `src/finwatch/db/repositories.py`.

---

## 7. P0 — Filing Preprocessor / Section Router (deterministic)

Downloads the primary document, detects form type, splits into sections with **canonical
section keys**, records char offsets/HTML element ids/text hashes, detects amendments and
furnished items. P1 receives already-labeled sections and never guesses where MD&A lives.

**Canonical section keys (the recurring gotcha — a 10-Q's MD&A is Part I Item 2, NOT
Item 7):**

| Form | Location | `section_key` |
|---|---|---|
| 10-K | Item 1 / 1A / 3 / 7 / 7A / 8 / 9A | `business` / `risk_factors` / `legal` / `mdna` / `market_risk` / `financials`+`auditor_report`+`notes` / `controls` |
| 10-Q | Part I Item 2 (**not Item 7**) | `mdna` |
| 10-Q | Part I Item 3 / 4; Part II Item 1 | `market_risk` / `controls` / `legal` |
| 10-Q | Part II Item 1A (material changes vs latest 10-K only) | `risk_factor_changes` |
| 8-K | Each Item present | `item_<number>` e.g. `item_4_02` |

Amendments (`/A`) link `amends_accession`. Furnished (Item 2.02/7.01) sets
`is_furnished=1`, feeding the P1 severity prior (T2 in §11). P0 also produces the
paragraph-level risk-factor diff (added/removed/modified) against the prior comparable
filing — same base form + same section key (a 10-Q diffs against the prior 10-Q, not the
full 10-K it's already a delta of — deliberate, see `preprocessor.py` docstring).
Implementation: `src/finwatch/preprocess/`.

---

## 8. XBRL Normalization Layer (`src/finwatch/xbrl/`)

Source: SEC `companyfacts` JSON per CIK. Concept map (priority-ordered us-gaap/dei tags
per concept): `xbrl/concept_map.yaml`, mirroring `CONCEPT_MAP` in `normalize.py`.

**Normalization rules (the recurring principles — don't relitigate these on a future edit):**
1. **Consolidated only** — dimensional facts are stored for segment work but excluded from
   headline concepts.
2. **Period alignment** — durations for flows (revenue, CFO), instants for stocks (assets,
   cash); resolved **per accessor**, not once globally (a concept's priority tag can carry
   only quarterly data while a fallback tag has the annuals — each accessor falls through
   independently).
3. **Amendment supersession** — latest `filed` wins; superseded rows flagged, never deleted.
4. **Units & scale** — respect `unit_ref`/`decimals`; never re-scale silently.
5. **Sector heuristic** — SIC 6000–6999 → `financial` (6300–6499 `insurance`, 6798 `reit`);
   4900–4999 → `utility`; else `general`. `is_financial` gates metric applicability.

Point-in-time: metrics are computed from facts filed on or before `as_of` — a historically-
dated run never sees a later-filed fact or restatement (`metrics/service.as_of_facts`).

---

## 9. Metrics Engine (`src/finwatch/metrics/`) — sector-aware, versioned

Every metric returns the universal envelope: `status: computed | unavailable |
not_applicable`. The distinction is **load-bearing** — it's what stops a bank from
tripping a false data gate on a metric (e.g. EV/EBITDA) that's conceptually meaningless
for it. `unavailable` = data missing; `not_applicable` = wrong metric for this issuer.

Full catalog (Piotroski F, Altman Z/Z″, Beneish M, valuation percentiles, PEG, FCF yield,
Graham number, position metrics, rebalance check, …): `metrics/formulas.py`. **Ambitious
core, conservative surface** — the digest's "Verified numbers" shows only the starter
set (`revenue_growth`, `net_income_trend`, `cfo_trend`, `liquidity_basics`,
`share_count_change`, `simple_leverage`); the rest compute every run, persist to
`computations`, and feed the P3 matrix + `finwatch shadow report` / `--signals`.

---

## 10. Shared Foundation Prompt Rules (`prompts/foundation.md`, prepended to P1/P2/P3)

The rules every stage prompt obeys, in one place because they're what makes the trust
layer trustworthy — full verbatim text lives in the file, this is the compressed form:

R1 numbers only if verbatim in input, never computed/estimated by the LLM · R2 claim
graph: EVIDENCE (verbatim-anchored, full provenance) vs JUDGMENT (`basis_claim_ids`,
never new facts) · R3 confidence calibrated; `insufficient_data`/`not_assessable` are
first-class answers · R4 no price prediction · R5 educational posture, never an
instruction to trade · R6 JSON only, per stage schema · R7 honesty over helpfulness —
report truncated/malformed input, don't produce plausible-looking analysis anyway.

---

## 11. P1 — Filing Event Extractor (`prompts/P1_extractor.md`)

Runs once per new filing. Role: a materiality analyst (SEC reasonable-investor standard)
who extracts, classifies, and flags — never editorializes or predicts. Tasks: T1 classify
8-K items against a base-severity prior with a **hard floor** (never rate below HIGH
regardless of framing: Item 4.02, going-concern language, auditor resignation, Item 1.03,
Item 3.01, Item 2.04, material weakness) · T2 section analysis (10-Q risk_factor_changes
content is inherently notable, since it's already the delta vs the latest 10-K) · T3
quantitative evidence as verbatim EVIDENCE claims · T4 language/tone shifts + red-flag
lexicon · T5 `guidance_direction` — a formal contract P2 carries forward and P3/matrix
reads directly · T6 red-flag register (empty is a common, valid result — never manufacture
flags). Full task detail + output schema: `prompts/P1_extractor.md`, `llm/schemas.py`.

---

## 12. P2 — Portfolio Impact Explainer (`prompts/P2_impact.md`)

Runs when P1's `overall_severity` ≥ MEDIUM, or any non-empty red-flag register regardless
of severity (`pipeline/orchestrator.p2_gate`). Role: trace the *mechanism* by which new
information changes cash flows, risk, or competitive position — across 8 explicit
transmission channels (revenue, margin, capital structure, cash/working capital,
competitive moat, governance, cross-holding spillover, idiosyncratic-vs-systematic driver)
— never net them into one score. Normalizes `guidance_direction` (carried from P1),
`liquidity_read`, `net_direction` as formal contracts for P3. Thesis is optional by
design; missing thesis degrades gracefully with a fixed user-facing note, never onboarding
friction. Full detail: `prompts/P2_impact.md`.

---

## 13. P3 — Signal Engine (matrix = code, LLM = rationale, SHADOW by default)

### 13.1 The decision matrix is deterministic Python (`signals/matrix.py`)

Vocabulary: `CAUTION_ORDER = [STRONG_REVIEW_SELL, TRIM, HOLD, ACCUMULATE]` (index 0 =
most cautious) · `POSTURE_MAP` to the user-facing posture · `CRITICAL_DOC_FLAGS` (8 codes:
bankruptcy, delisting, acceleration, non-reliance, going-concern, auditor resignation,
material weakness, critical-tier cyber incident).

Rule precedence (first match sets the base; caps apply after) — **authoritative,
exhaustively tested in `matrix.py` + `test_signals_matrix.py`; this is a summary only**:
M0 ownership gate (not owned → `NOT_APPLICABLE_WATCHLIST`) → M1 document-level critical
red flags, zero metrics required → M2 thesis broken → M4 solvency deterioration → M6 rich
+ deteriorating → M7 accumulate gate (requires thesis intact, strong fundamentals, cheap
valuation, underweight, **and no red flags of any kind**) → M8 default HOLD → M5
concentration cap (monotone, toward caution only, fires on **over**-weight only — an
underweight position drifting further from target is never capped toward caution).

`insufficient_data` fires only when P1 `extraction_confidence` is low AND gaps block
assessment. Missing metrics alone → HOLD with skipped-rule reasons logged. Alert quality
over coverage theater.

### 13.2 Shadow mode & `--signals`

Every evaluation writes to `signal_shadow_log` unconditionally. Default digest renders
POSTURES ONLY; `trade_action` is always null. `--signals` additionally renders the
hypothetical-signal block, labeled unvalidated/educational. Promotion policy (≥100 logged
evals + human audit of ≥20 + acceptance gates): README.md.

### 13.3 P3 LLM prompt — rationale writer only (`prompts/P3_rationale.md`)

The engine has already decided the posture and signal; the LLM writes the plain-English
rationale, the strongest counter-evidence (mandatory), and "what would change this." Its
**only** lever on the decision: it may request a one-notch escalation **toward caution**
with justification — the engine applies and logs it, and can never move toward
aggression. Forbidden vocabulary + price-target language: `core/types.py`
`FORBIDDEN_VOCABULARY`, enforced by V5.

---

## 14. Verifier (`src/finwatch/verify/`) — deterministic code, the compile pass

**Not an LLM.** Runs after P1/P2/P3, before the digest. Never edits content — no silent
fixes, ever. On blocking FAIL → regenerate the failing LLM stage (max 2 retries) → still
failing → digest renders "⚠ manual review required."

- **V1 numeric provenance** — every number in rendered text/claims must match a
  `computations` row, an `xbrl_facts` row, or an evidence snippet, within precision.
  Orphans are a blocking fail.
- **V2 accounting identities** (A=L+E, cash tie-out, income-statement ordering) — a
  **non-blocking data-quality warning**, not a gate: these legitimately false-fail on
  common structures (noncontrolling interest, restricted cash) that don't mean the
  analysis is wrong, so they surface in the digest's "Open questions" instead of
  quarantining the filing. The cash tie-out only applies to annual filings.
- **V3 rule-logic re-derivation** — re-runs `matrix.evaluate()` on the stored decision;
  requires an exact match of posture, signal, rules fired, rules skipped, and caps.
- **V4 citation integrity** — exact-substring check of every evidence snippet against its
  declared span + text hash.
- **V5 schema & hygiene** — pydantic validation, disclaimer present verbatim, forbidden-
  vocabulary + price-target regex scan, `trade_action` must be null by default.

Mutation-test battery (the DoD for this module): `tests/test_verifier_mutations.py` seeds
known corruptions and asserts each fails on the correct check id.

---

## 15. Digest Renderer (`src/finwatch/digest/`) — deterministic markdown

Pure function of the DB — **no LLM calls at render time**. Sections: header · critical
red flags (claim-backed, EDGAR-linked) · what changed · thesis impact · verified numbers
· open questions (incl. V2 data-quality notes) · boring filings (one collapsed line —
silence is a feature) · shadow signals (only with `--signals`). Full section detail +
sample output: `digest/render.py`, `docs/sample_digest.md`.

---

## 16. Golden Set & Eval Harness (`evals/`)

The golden set is the product's conscience — real filings pinned by accession number in
`evals/golden_set/manifest.yaml`, each with recorded expected P1 output. Do not invent
accession numbers; locate real examples via EDGAR full-text search. Scoring: critical
extraction recall (must be 100% — missing a going-concern is disqualifying), citation
integrity, JSON validity, verifier pass rate, false-alarm rate on boring cases, cost.
**Model selection is empirical, the only rule:** `finwatch eval --models a,b,c` and pick
the cheapest model that clears every threshold — model choice is config, not
architecture, re-run the bake-off whenever the model landscape shifts.

---

## Roadmap — explicitly out of scope (do not start unprompted)

MCP server wrapper · broker CSV import/sync · Form 4 insider tracking · news APIs ·
sector-relative valuation · earnings-call transcripts · deep symbolic math-as-compiler
(constraint-checking over reasoning chains).

## Where to find things

| Topic | Authoritative source |
|---|---|
| Status, quickstart, CLI walkthrough, acceptance gates, shadow promotion policy | `README.md` |
| Module tiers, file tree, data flow, fixed interface contracts | `SYSTEM_DESIGN.md` |
| Trust-critical files + test-guarded norm | "Trust-critical code" box above · `SYSTEM_DESIGN.md` §1, §6 |
| Historical build-time trust-layer snapshot (not live law) | `CORE_CODE.md` |
| Full v0.2 build spec (everything this file used to hold in full) | `docs/CLAUDE_v0.2_full_spec.md` |
| DB schema (DDL) | `src/finwatch/db/schema.sql` + `src/finwatch/db/migration_*.sql` |
| XBRL concept map | `src/finwatch/xbrl/concept_map.yaml` |
| Metric formulas (full catalog) | `src/finwatch/metrics/formulas.py` |
| LLM prompts (versioned, verbatim) | `src/finwatch/prompts/*.md` |
| Signal matrix (pure function) | `src/finwatch/signals/matrix.py` |
| Verifier checks | `src/finwatch/verify/checks.py` |
| RipplX web UI and local API | `web/`, `src/finwatch/web/`, `src/finwatch/presentation/` |
| Golden set | `src/finwatch/evals/golden_set/manifest.yaml` |
| Production pipeline runner | `src/finwatch/pipeline/run.py` |


Anti AI Narcissism
Researches has shown that AI is not good and honest at analyzzing, reviewing and critiquing heir own work because they an be biased and can be narcissistic towards their own work. Be mindful of this AI weakness, while reviewing your own work and try to be super critical, honest about the quality of the work. Our goal is to icrease the quality of the work, not to feel self happy by wrong self validation.

— end —
