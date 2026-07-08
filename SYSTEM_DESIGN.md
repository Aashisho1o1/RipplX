# finwatch — SYSTEM_DESIGN.md
## System Design Plan v0.2 — module map, criticality tiers, and integration contract

This document rides alongside **CLAUDE.md** (project context) and **CORE_CODE.md** (a historical
build-time snapshot of the trust layer — no longer live law). Precedence today:

> **shipped code + its tests > CLAUDE.md > this document (map & rationale) > agent judgment.**
> (`CORE_CODE.md` is historical only; the shipped source has since diverged via bug fixes.)

---

## 1. Criticality tiers

The tiers describe how much *care* a file needs, not how locked it is. **Every file is editable;
the codebase is flexible.**

- **⚙ TIER 1 — TRUST-CRITICAL (test-guarded).** The deterministic trust layer (see §6). Editable
  like anything else, but its failure mode is silent, so a change must keep the executable-spec
  tests green and get a real review. These files were originally pre-written and transcribed
  verbatim from `CORE_CODE.md` at build time; that mirror is now historical, not a constraint.
- **🔧 TIER 2 — CORE APP CODE.** The interface contracts in §4 are still real — other code relies
  on them, so change them deliberately and update both sides.
- **🧱 TIER 3 — STANDARD CODE.** Ordinary engineering within the CLAUDE.md working conventions
  (deps, tests, commit discipline).

## 2. Annotated file tree

```
finwatch/
├── CLAUDE.md · SYSTEM_DESIGN.md · CORE_CODE.md      (specs — read all three first)
├── pyproject.toml, .env.example, LICENSE, README.md  🧱
├── prompts/
│   ├── foundation.md, P1_extractor.md, P2_impact.md, P3_rationale.md   🔧 (verbatim from CLAUDE.md §§10–13)
├── src/finwatch/
│   ├── core/
│   │   └── types.py            ⚙ TIER 1  — enums, constants, caution order, disclaimer
│   ├── xbrl/
│   │   ├── normalize.py        ⚙ TIER 1  — FactStore: the XBRL boss fight
│   │   └── concept_map.yaml    🔧 generated to mirror CONCEPT_MAP in normalize.py
│   ├── metrics/
│   │   ├── envelope.py         ⚙ TIER 1  — MetricResult / InputUsed contracts
│   │   └── formulas.py         ⚙ TIER 1  — all metric math, sector-aware
│   ├── signals/
│   │   └── matrix.py           ⚙ TIER 1  — deterministic decision engine
│   ├── verify/
│   │   └── checks.py           ⚙ TIER 1  — V1–V5 deterministic verifier
│   ├── ingest/                 🔧 — EDGAR client (UA, ≤8 req/s, backoff, raw cache),
│   │                                ticker→CIK, backfill, poller, stooq.py (PriceProvider)
│   ├── preprocess/             🔧 — P0: form router, canonical sections, offsets,
│   │                                furnished/amendment flags, risk-factor differ
│   ├── llm/                    🔧 — litellm router, prompt loader (versioned),
│   │                                pydantic stage schemas mirroring CLAUDE.md schemas
│   ├── pipeline/               🔧 — orchestrator: ingest→P0→P1→metrics→P2→verify→(P3)→digest
│   ├── claims/                 🔧 — claim-graph persistence helpers
│   ├── digest/                 🔧 — deterministic markdown renderer (CLAUDE.md §15)
│   ├── db/                     🧱 — schema.sql (CLAUDE.md §6 verbatim), migrations, repos
│   └── cli.py                  🧱 — Typer surface (CLAUDE.md §5)
├── evals/                      🔧 — golden-set manifest + harness (CLAUDE.md §16)
└── tests/
    ├── test_signals_matrix.py       ⚙ TIER 1 — executable spec of the matrix
    ├── test_verifier_mutations.py   ⚙ TIER 1 — executable spec of the verifier
    └── (everything else)            🧱 — fixtures, unit tests per module
```

## 3. Data flow (who calls whom)

```
ingest/ ──raw JSON/HTML──► db/ ──► preprocess/(P0) ──sections──► llm/(P1) ─┐
   │                                                                       │ claims
   └─companyfacts──► xbrl/normalize.FactStore ──► metrics/formulas ──► MetricsBundle
                                                                          │
                llm/(P2) ◄── P1 output + holdings ◄───────────────────────┘
                   │
                   ▼
        signals/matrix.evaluate(record, extraction, impact, metrics)   [pure function]
                   │ Decision
                   ▼
        llm/(P3 rationale)  →  verify/checks.run_all(bundle)  →  digest/
                                        │
                                        └── V3 re-runs matrix.evaluate() to audit P3
```

Key property: `matrix.evaluate` and every function in `metrics/formulas.py` are **pure** —
same inputs, same outputs, no I/O. That is what makes V3 re-derivation and the shadow log
trustworthy.

## 4. Fixed interface contracts (Tier 2 code must conform to these)

1. **`FactStore.from_companyfacts(cf_json: dict) -> FactStore`** — ingest hands the raw SEC
   companyfacts dict straight in; no pre-massaging.
2. **`PriceProvider` protocol** (defined in `metrics/formulas.py`):
   `close_on_or_before(ticker: str, date_iso: str) -> float | None`. Implement it in
   `ingest/stooq.py` (EOD CSVs, cached into the `prices` table). Tests use a fake.
3. **`compute_all(store, sector, *, ticker, price_provider, holding=None, portfolio=None,
   as_of) -> MetricsBundle`** in `formulas.py` is the ONLY entry point the pipeline calls
   for metrics. Persist each `MetricResult` to the `computations` table verbatim
   (`model_dump_json()`).
4. **`matrix.evaluate(record: Record, extraction: ExtractionSummary, impact: ImpactSummary,
   metrics: MetricsBundle) -> Decision`** — the pipeline builds the three summary models from
   P1/P2 JSON (adapters live in `pipeline/`, thin and dumb) and never adds logic of its own.
5. **Verifier entry point:** `verify.checks.run_all(bundle: VerifyBundle) -> VerificationReport`.
   The pipeline assembles `VerifyBundle` (rendered text, claims, computations, fact store,
   section texts, decision, stage JSONs) and acts on the report per CLAUDE.md §14
   (regenerate ≤2, else manual-review flag). The pipeline never edits content to make a
   check pass.
6. **Stage schemas:** `llm/schemas.py` pydantic models must round-trip the JSON schemas in
   CLAUDE.md §§11–13 exactly; V5 validates against them.

7. **Resumable execution:** `pipeline/progress.py` defines the small persisted stage ledger
   (`download` through `verify`). Completed P0/P1/P2/P3 artifacts are reused after downstream
   failures; explicit parse or analysis reruns invalidate stale downstream artifacts first.

## 5. Extension points

New metrics → add a function in `formulas.py` returning `MetricResult` and register in
`compute_all`. New matrix rules → new rule block with an explicit per-rule gate + tests; caps
remain monotone toward caution. New data sources → new `ingest/` module feeding existing
tables. The RipplX web app lives in the separate `web/` package and consumes structured
projections from `src/finwatch/presentation/` through the local-only `src/finwatch/web/`
adapter. It calls existing services for writes and never duplicates trust-layer logic.

## 6. Why these modules are trust-critical (rationale for reviewers)

They share two properties that earn the extra, test-guarded care: (a) a subtle error is
**silent** — wrong-but-plausible numbers, a mis-ordered rule, a tolerant-when-it-shouldn't-be
matcher; (b) they define the product's trust promise ("verified"). They are pure logic with
stable interfaces — which is exactly why the executable-spec tests can pin their behavior; the
tests, not a freeze, are what keep them honest. (Historically they were pre-written and
transcribed verbatim from `CORE_CODE.md` so the initial build couldn't corrupt them; that
scaffolding is spent — edit them freely, keep the specs green.) Everything else fails loudly and
iterates cheaply.
