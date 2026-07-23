# KERNEL.md — The Publication Kernel

> **Status:** research spec v0.1 (2026-07-21). This document *names and freezes* semantics the
> shipped code already enforces, and clearly marks the places where the spec is ahead of the code.
> Per the project ground-truth rule, shipped code + tests remain authoritative: if this file ever
> disagrees with shipped behavior and the gap is not listed in "Spec ahead of code", fix this file.

---

## Why this document exists

finwatch is compiler-shaped: untrusted filing + probabilistic LLM proposal → typed candidates →
deterministic binding, checking, pruning → canonical artifact → certificate. What turns
"compiler-shaped" into "actually a compiler" is one artifact: a **closed claim language with a
decidable admissibility judgment**. This page is that artifact. Everything else — the claim IR,
the trust grades, the certificate payload, the standalone checker, benchmark hard gates — is a
corollary: the IR is the language's terms, the certificate is a serialized derivation, the checker
is a second (small) implementation of the judgment, and a benchmark gate is "the judgment holds on
replay."

The product compression of this page:

> **Three changes maximum. Six financial deltas. Every published item compiles — or it does not
> ship, with a typed reason.**

---

## The principle: publish only under ∀

**finwatch publishes a claim iff it is true under *every* reading consistent with what the SEC
artifacts literally assert** — universal quantification over three axes:

1. **Precision** — a tagged value asserts an interval, not a point (its XBRL `decimals`).
2. **Text** — a quotation asserts exact bytes at exact offsets in a hash-pinned section.
3. **Time** — a fact exists only if it is provably available (`filed <= as_of`, fresh, unsuperseded).

An LLM is an ∃/argmax machine: it finds *a* plausible reading. The kernel is a ∀ machine: it
certifies *all* readings. The LLM proposes; the kernel disposes. Fail-closed behavior is the same
principle under partial information: when a precondition cannot be proved, the claim is
`unavailable` or withheld — unknown never rounds up to true.

---

## The judgment

The kernel is one decidable relation:

```
Γ ⊢ claim : grade
```

where `Γ` is the trusted environment (point-in-time fact store, stored canonical sections + hashes,
policy version) and `grade` lies in the lattice:

```
verified  >  supported  >  ai_selected          (trust order)
plus terminal states:  unavailable | withheld
```

- `verified` — deterministic computation or exact source relationship (metric values, directions,
  quote anchoring, hashes).
- `supported` — exact evidence exists; the semantic interpretation remains AI/human judgment.
- `ai_selected` — ranking/importance judgment. **Never promotable.** Importance is not decidable;
  it stays `ai_selected` forever, and the UI says so.
- `unavailable` — a precondition could not be proved (missing fact, stale date, denominator
  interval containing zero).
- `withheld` — publication policy failed (gate or pipeline failure), with a typed reason.

**Design obligation:** the claim language is *not Turing-complete*. Finite ASTs, no recursion, no
fixpoints, no model-generated code execution. Every check is a bounded comparison against stored
artifacts, so type-checking a finite term terminates. The day arbitrary generated code enters the
language, decidability — and this entire page — dies.

---

## The eight rules

**R1 — Fact interval semantics.** A value `x` reported with XBRL `decimals = d` asserts only

```
I(x, d) = [ x − ½·10⁻ᵈ ,  x + ½·10⁻ᵈ ]        (d = INF ⇒ the point {x})
```

Missing rounding metadata never becomes zero slack; it becomes a conservative interval or
`unavailable`.

**R2 — Typed lifting.** Arithmetic is defined only on compatible `(unit, period, entity,
dimensions)`; results carry propagated intervals. Division by an interval containing zero is
`unavailable`, never an invented number. USD/USD is dimensionless; duration and instant periods
never mix silently.

**R3 — Direction by separation** *(the archetype rule)*. A directional claim compiles only when
the intervals separate:

```
up    ⟺  inf I_current > sup I_prior        (equivalently  Δ > slack_c + slack_p)
down  ⟺  sup I_current < inf I_prior
else  ⟺  indistinguishable within stated precision — direction is NOT derivable from the filing
```

The overlap case is *indeterminate*, not "flat": overlapping intervals prove neither equality nor
change.

**R4 — Quote semantics.** A quotation claim asserts `section_text[start:end] == snippet` against
the stored canonical section whose SHA-256 is pinned beside it. The interpretation set is a
singleton — exact bytes or fail. Headlines authored by the model contain no quantities; numbers
appear only inside declared exact evidence spans or from persisted computations.

**R5 — Admissibility of Γ (point-in-time).** A fact enters `Γ` only if `filed <= as_of` is
provable, its age is within the freshness window (550 days for annual legs, 200 for
instant/share-count/quarterly legs), and it survives amendment supersession (latest `filed` wins,
accession tie-break). Future, stale, missing, or malformed dates exclude the fact — ∀ over "was it
available?" means when in doubt, it wasn't.

**R6 — Grading.** Every published surface element decomposes into claims graded by the judgment;
one vague "verified" badge never mixes provenance, arithmetic, interpretation, and importance. The
metric envelope's `computed | unavailable | not_applicable` and the filing outcome enum are the
judgment's terminal states made visible.

**R7 — Termination (the guide).** The kernel cannot loop (R6 note + non-Turing-complete language).
The only loop is the neural–symbolic search loop — LLM proposes, compiler returns typed errors,
bounded repair — i.e. CEGIS with an LLM synthesizer. It halts because a well-founded measure
strictly decreases:

```
μ(state) = ( fuel , unresolved obligations )   ∈  ℕ × ℕ,  lexicographic
```

Fuel (8 Generator turns, 6 Generator tool requests, 1 shared repair, 2 Skeptic tool requests)
decreases on every model action; within fixed fuel, repair is **drop-monotone** — a finding with a
validated objection is repaired at most once or pruned, never re-litigated, and the compiler is
the sole judge. Fuel exhaustion fails closed. Therefore the pipeline is a *total function*: every
(filing, model) pair terminates in one member of the closed outcome enum
`published | no_findings | findings_dropped | withheld_gate | pipeline_failed | not_analyzed`.
The LLM's role is precisely the **search policy** over an astronomically large binding space
(which concept, which section, which spans) — it makes search tractable and contributes zero
trust. Policy supplies direction; the measure supplies termination; the kernel supplies truth.

**R8 — Soundness and replay.** The certificate is (eventually — see gaps) a serialized derivation
of the judgment. The soundness statement finwatch claims, and the only one:

> If the checker accepts a certificate, then every claim graded `verified` in it holds in **all**
> interpretations consistent with the cited SEC artifacts' literal content and stated precision.

In one sentence: **we do not prove the filing is true; we prove we added nothing to it.**

---

## Traceability — the kernel already runs

| Rule | Shipped implementation | Guarding test | Status |
|---|---|---|---|
| R1 | `metrics/formulas.py:48-58` — slack computed as `Decimal("0.5").scaleb(-exponent)`, exactly ½·10⁻ᵈ; missing metadata → conservative/None path | `tests/test_harness.py:302` (`test_direction_uses_decimal_arithmetic_at_exact_uncertainty_boundaries`) | shipped |
| R2 | six starter formulas in `metrics/formulas.py`; envelope rejects NaN/∞ (`metrics/envelope.py:50-67`); unavailable-not-invented denominators | `tests/test_starter_metric_integrity.py`, `tests/test_metrics_service.py` | partial — interval propagation exists for direction, not yet for every derived ratio; components untyped |
| R3 | Decimal-exact `Δ` and `slack_c + slack_p` at `metrics/formulas.py:81-85`; decision procedure `deterministic_direction` at `metrics/envelope.py:74-82`; compiler checks model `direction` against it | `tests/test_harness.py:284` (`test_direction_boundary_is_strict`) | shipped — naming gap: overlap currently returns `"flat"`; spec says *indistinguishable* (see gaps) |
| R4 | `presentation/canonical.py:110-129` — `section.text[start:end] != snippet` rejects; SHA-256 pinned; V4 at `verify/checks.py:350`; authored-headline number policy V1 at `verify/checks.py:255` | `tests/test_verifier_mutations.py`, `tests/test_presentation_fail_closed.py` | shipped |
| R5 | `xbrl/normalize.py` point-in-time normalization; supersession "latest `filed` wins" at `xbrl/normalize.py:368-369`; `ANNUAL_FRESHNESS_DAYS = 550` at `metrics/formulas.py:20`, `RECENT_FRESHNESS_DAYS = 200`, enforced at `metrics/formulas.py:117-153` | `tests/test_metrics_service.py`, `tests/test_starter_metric_integrity.py` | shipped |
| R6 | envelope `computed\|unavailable\|not_applicable`; `FilingOutcome` in `presentation/models.py`; "AI-selected changes (evidence verified)" UI labeling | `tests/test_presentation.py`, `tests/test_launch_projection.py` | partial — grades exist but are distributed, not one exported lattice |
| R7 | fuel constants `llm/harness.py:450-452` (`MAX_GENERATOR_TURNS = 8`, `MAX_TOOL_CALLS = 6`, `MAX_SKEPTIC_TOOL_CALLS = 2`); one shared repair; drop-monotone prune via `compile_draft` (`verify/compiler.py:223`) + `DroppedFinding` (`:33`); compiler sole judge; Skeptic one-directional | `tests/test_harness.py` (budget/termination cases), `tests/test_run.py` | shipped — unnamed until now |
| R8 | V1/V4/V5 (`verify/checks.py:255/350/382`), final-DTO recheck (`verify/presentation.py`), `certificate.v2` from the immutable attempt snapshot (`presentation/service.py`) | `tests/test_verifier_mutations.py`, `tests/test_presentation_verifier.py` | partial — certificate is a receipt (hashes), not yet a full derivation; no standalone checker |

The evidence-over-dogma reading of this table: the theorem is not aspirational — it is running in
production with mutation tests. The remaining work is *reification*: export the judgment as one
named, versioned surface instead of semantics distributed across five modules.

---

## Non-claims (honest scope — never weaken this list)

The kernel proves provenance, exactness, typed arithmetic, rounding propagation, temporal
admissibility, and termination. It does **not** and will never claim to prove:

- that the issuer's filing is economically true;
- that management tagged the ideal XBRL concept;
- that the AI's interpretation of a quotation is correct (that stays `supported`);
- that a selected change is objectively one of the three most important (that stays `ai_selected`);
- that any investment action follows.

---

## Spec ahead of code (roadmap, not shipped — do not treat as current behavior)

1. **Exact decimals end-to-end.** Core slack/delta math is already `Decimal`-exact, but values
   cross float boundaries at the edges: `FiniteFloat` in `metrics/envelope.py`, `value REAL` in
   `db/schema.sql:73`. The certificate must eventually carry exact decimal *strings*; storage can
   migrate later.
2. **Four-valued direction wording.** `metrics/envelope.py:82` returns `"flat"` on interval
   overlap. Presentation already words this carefully ("not certified within SEC rounding slack"),
   but the envelope's internal name should become *indistinguishable*: overlap is indeterminacy,
   not proven equality.
3. **Typed claim IR.** `components: dict[str, Any]` in the envelope, and finding/metric/direction
   claims as one typed tree (SourceArtifact, FactRef, QuoteClaim, MetricClaim, DirectionClaim,
   ChangeClaim, CoverageClaim, SelectionJudgment) rather than distributed fields.
4. **Derivation-carrying certificate + standalone checker.** `certificate.v2` proves byte
   integrity; the target payload adds exact fact strings, contexts/units/periods, selected *and
   rejected* bindings, formula AST + version, intermediate intervals, and compiler/policy
   versions — enough for an independent ~small checker to reproduce every `verified` claim from
   the certificate plus public SEC artifacts alone. The checker, not the pipeline, is the trust
   anchor (proof-carrying-code split: producer arbitrarily complex and untrusted, checker small
   and trusted).

Sequencing: **language → checker → benchmark**. A FilingDeltaBench-style benchmark's hard gates
(100% citation validity, 100% derivation validity, 0% unsupported publication) are unmeasurable
until this page defines "correct"; once frozen, an ablation ladder (LLM alone → +retrieval →
+compiler → +Skeptic → full gate) becomes cheap to run. External alignment worth taking: adopt the
XBRL Calculations 1.1 interval convention for R1 (standards credibility, and Arelle becomes a free
differential-test oracle for the kernel).

— end —
