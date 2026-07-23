# RipplX / finwatch — Frontend Truth & Rigor Implementation Plan

**Objective.** The deterministic trust layer already computes far more than the browser shows, and the browser already asserts several things the backend does not support. This plan closes both gaps in six independently shippable commits: it stops the UI naming a pipeline crash as a verification refusal, stops it announcing "nothing important changed" when the gate deleted every candidate, renders the V1/V4/V5 publication gate that is already serialized and thrown away, stops hiding filings and metrics that passed, gives a cold-start visitor something real to look at, tightens outcome encoding and copy, and fixes the accessibility and dead-code debt left behind by the last redesign. No publication check is weakened anywhere; every change either renders data the gate already produced or narrows what is projected.

---

## 0. Read this first — rules of engagement

Work from this section alone. Do not re-derive project policy from the repo.

### 0.1 Non-negotiable project rules

- **`AGENTS.md` and `CLAUDE.md` are byte-identical mirrors.** Any change to a documented contract must be applied identically to **both** files **in the same commit**. Verify with `diff AGENTS.md CLAUDE.md` (must print nothing) before every commit that touches either.
- **Never weaken the publication gate to make the UI nicer.** Presentation may never edit content to make a check pass. Every branch that withholds today must still withhold.
- **`PresentationService` is the sole DB→user-content projection.** `digest/render.py` must serialize the *same* `BriefView` and must not independently reload analyses, claims, or computations. If you change a brief-level field, check whether `digest/render.py` needs the mirrored change.
- **Filing/LLM text renders as escaped React text.** Never `dangerouslySetInnerHTML`.
- **The launch UI never emits a trade action, price target, P3 posture, or shadow signal.**
- **Raw model output, provider exceptions, and persisted raw stage details never cross the API boundary.** Job/API failures use fixed, user-safe messages only.
- **Ask before adding a dependency.** No workstream here adds one. Prefer a direct readable implementation over new abstraction.
- **Deletions are complete or they are not deletions.** Imports, exports, types, tests, fixtures, DTOs, routes, UI, CSS, docs, and CI references go together.
- **Plan → implement → focused tests → full suite → conventional commit.** Never knowingly leave the suite red. No live network or LLM calls in ordinary tests.

### 0.2 Trust-critical files — extra care required

```
src/finwatch/llm/schemas.py           src/finwatch/verify/checks.py
src/finwatch/llm/stages.py            src/finwatch/verify/presentation.py
src/finwatch/xbrl/normalize.py        src/finwatch/presentation/canonical.py
src/finwatch/metrics/formulas.py      src/finwatch/core/types.py
src/finwatch/metrics/envelope.py      tests/test_verifier_mutations.py
```

Only two workstreams touch this list:

- **W1** modifies `verify/presentation.py` (four additive invariants) and `presentation/canonical.py` (carries new discriminator fields). **Required adversarial tests: W1-T5 and W1-T6** — they construct DTOs that claim one state in `withheld` and a different state in `outcome`/`withheld_kind` and assert the verifier rejects them.
- **W3** modifies neither. It adds a presentation-only `withheld` row state in `presentation/models.py`; `metrics/envelope.py` stays three-valued and is **not** touched. **Required adversarial test: W3-T3** — asserts the tampered payload of a re-validation failure appears nowhere in the serialized DTO.

`verify/checks.py`, `verify/compiler.py`, `metrics/formulas.py`, `metrics/envelope.py`, `llm/*`, and `core/types.py` are **not modified by any workstream in this plan**. If a diff touches them, stop.

### 0.3 Commands

```bash
# backend
uv run pytest -q            # or: .venv/bin/python -m pytest -q
uv run ruff check .         # line-length 100; presentation/* is NOT ruff-excluded

# frontend
cd web && npm test -- --run
cd web && npm run typecheck
cd web && npm run build
cd web && npm run check:classnames   # exists only after W6
```

### 0.4 Commit conventions

One conventional commit per workstream (`feat:` / `fix:` / `test:` / `chore:`), including code, tests, and both doc mirrors. Exact messages are given per workstream.

---

## 1. Execution order and dependency graph

| # | Workstream | Depends on | Effort | Commit prefix |
|---|---|---|---|---|
| 1 | **W1 — Stop asserting false things about system state** | — | ~1 day | `fix:` |
| 2 | **W3 — Stop hiding filings and metrics that passed** | W1 (uses `FilingDigestEntry.outcome`) | ~1 day | `feat:` |
| 3 | **W2 — Render the V1/V4/V5 verification gate** | — (independent; sequenced here to avoid FilingPage/global.css churn) | ~half day | `feat:` |
| 4 | **W4 — First-run experience** | W3 (BriefPage section structure) | ~half day | `feat:` |
| 5 | **W5 — Outcome encoding and copy precision** | W3 (`_validated_metrics`, `MetricTable` summary) | ~1 day | `fix:` |
| 6 | **W6 — Accessibility and dead-code hygiene** | all (deletes `answer_posture` that W1/W3 still write) | ~half day | `chore:` |

**Why this order.**

1. **Truth-in-copy before new surfaces.** W1 introduces the `outcome` / `withheld_kind` discriminators that every later workstream buckets on. Building the reviewed-filings list (W3), the onboarding checklist (W4), or the outcome banner refinements (W5) on top of the old `withheld`/`findings` inference would mean writing that inference twice.
2. **DTO changes before the UI that consumes them.** W1, W3, and W4 each add fields to `BriefView`/`FilingDigestEntry`. Landing them in that order means `web/src/types.ts` is edited three times additively rather than being rewritten under a moving backend.
3. **W2 is genuinely independent** — it touches only `FilingDetailView`/`VerificationCheckView` and adds one new component. It is sequenced third so its `FilingPage.tsx` and `global.css` insertions land after W1/W3's edits to those files, keeping conflicts textual rather than semantic.
4. **W5 is polish on surfaces the earlier commits create.** It deletes `showComputedMark` from a `MetricTable` that W3 has already extended, and refines an outcome banner whose sibling states W1 defined.
5. **W6 last** because it deletes `answer_posture` end to end — a field W1 and W3 still assign — and rewrites tokens/CSS that every prior workstream added rules to.

### 1.1 Merged changes (deduplication log)

Two or more specs touched the same lines. Each is now owned by exactly one workstream; the later workstream carries a cross-reference.

| Line(s) | Owner | Superseded by | Note |
|---|---|---|---|
| `BriefView.boring_filings` | **W3** replaces it with `reviewed_filings: list[FilingDigestEntry]` | W1 keeps `boring_filings` (with the pluralization fix) and adds `gate_removed_filings`; W3 then converts | W1 lands first; W3-C5 buckets off W1's `outcome` |
| `PresentationService.companies()` | **W5-C11** owns the final body | W1-C7 (form filter + rename), W3-C6 (`STARTER_METRICS` denominator) fold in | W5-C11's code below is the merged version |
| `PresentationService._metric_rows` | **W3-C3** owns the split | W5-C10's `_validated_metrics` refactor is folded into W3-C3 | one refactor, not two |
| `web/src/components/MetricTable.tsx` | **W5-C4** owns the final file | W3-C10 (summary + 4 states), W6-C7 (`.trust.missing`) fold in | W5-C4's code below is the merged version |
| `BriefPage.tsx` section numbering | **W3-C11** owns 01/02/03/04 | W5-C8's anchors shift accordingly (see W5-C8 note) | |
| `MetricState` literal | **W3-C1** | W1/W6 do not touch it | |
| `answer_posture` | **W6-C12** deletes | W1-C6 and W3-C5 still assign it | see **Decision D3** |

---

## ⚠️ DECISIONS THAT ARE THE USER'S, NOT CODEX'S

Codex **must not** proceed on these without an explicit answer. Each reverses or amends a documented contract.

### Status

| Decision | Answer | Effect on the plan |
|---|---|---|
| **D1** Render the gate vs delete the field | ✅ **ANSWERED — Option A: RENDER** | Ship W2 in full. Amend §10 in **both** `AGENTS.md` and `CLAUDE.md` using the exact wording in W2's doc-updates subsection. Do **not** take Option B. |
| **D2** Ship the demo dataset hosted | ✅ **ANSWERED — YES, and also ship the static specimens** | Ship W4 in full, including **W4-C7** and `test_remote_sample_brief_serves_only_bundled_public_data`. Delete the `LOW-6` comment at `app.py:616` and the assertion at `tests/test_web_security.py:202-210` in the same commit. Keep the labelled static specimens as well — they cover the logged-in-but-unsynced state that the hosted demo does not. |
| **D3** Remove the posture chain end to end | ✅ **ANSWERED — remove end to end** | Ship W6-C9 and W6-C12 in full: delete `PosturePill.tsx` + its test, the `Posture` type in both `web/src/types.ts` and `presentation/models.py`, `BriefView.answer_posture`, its assignments in `service.py`, and the dead `answerTone` at `BriefPage.tsx:32`. This changes the `/api/brief` response shape — do it in the W6 commit only, not earlier. |
| **D4** Brief headline ordering | ✅ **ANSWERED — findings first** | The printed `answer` chain is superseded. See **W1-C6a** immediately below, which is authoritative wherever that chain appears (W1-C6 and the W3 rewrite). Adds test W1-T2a. |
| **D5** Presentation-only fourth metric state | ✅ **ANSWERED — Accept** | Ship W3's four-state `MetricState` (`computed \| unavailable \| not_applicable \| withheld`) in `presentation/models.py`. `metrics/envelope.py` stays three-valued and untouched. Amend §8 in **both** mirrors per W3's doc-updates subsection. |

---

### W1-C6a · D4 amendment — findings lead the headline (**authoritative; overrides the `answer` chain wherever it appears**)

Decision D4 was answered **"findings first"**. The `answer` if-chain printed in **W1-C6** — and again in the W3 change that replaces the `brief()` body — is superseded by the version below. Apply this ordering in whichever change you are executing; do not implement the printed ordering and then re-fix it.

**Rationale.** A verified critical disclosure must outrank an operational failure. But incomplete coverage must still be stated, because a user should not act on a brief without knowing part of it is missing. So findings lead the headline and coverage becomes a second sentence — rather than being dropped.

Add this helper next to the other module-level helpers in `src/finwatch/presentation/service.py` (it needs `plural_count`, already imported by W1-C6):

```python
def _coverage_sentence(
    gate_withheld: list[FilingDigestEntry],
    pipeline_failed: list[FilingDigestEntry],
) -> str:
    """State incomplete coverage without blaming the gate for a pipeline crash.

    The two causes never share wording: `gate_withheld` is a deterministic refusal
    to publish, `pipeline_failed` is an attempt that never produced a verification
    result at all.
    """
    parts = []
    if gate_withheld:
        parts.append(
            f"{plural_count(len(gate_withheld), 'filing')} withheld — could not be verified"
        )
    if pipeline_failed:
        parts.append(
            f"{plural_count(len(pipeline_failed), 'filing')} could not be analyzed — "
            "the pipeline did not complete"
        )
    return f"Coverage is incomplete: {'; '.join(parts)}."
```

Replace the whole `answer_posture = None` / `if …` / `else:` chain with:

```python
        # Findings lead: a verified critical disclosure outranks an operational
        # failure. Incomplete coverage is still reported, as a second sentence, so
        # the user never acts on a partial brief without being told it is partial.
        answer_posture = None
        if severe:
            answer = "A tracked company needs a critical review."
            answer_posture = "critical_review"
        elif published:
            answer = f"Important changes found in {plural_count(len(published), 'filing')}."
            answer_posture = "risk_review"
        elif gate_removed:
            answer = (
                f"Every proposed change in {plural_count(len(gate_removed), 'filing')} "
                "failed the evidence gate. Verified numbers still published."
            )
            answer_posture = "risk_review"
        elif analyzed:
            answer = (
                "Nothing important changed. "
                f"{plural_count(len(boring), 'routine filing')} reviewed."
            )
            answer_posture = "monitor"
        elif gate_withheld or pipeline_failed:
            # Nothing published and nothing routine: incomplete coverage IS the story.
            answer = _coverage_sentence(gate_withheld, pipeline_failed)
            answer_posture = "risk_review" if gate_withheld else "insufficient_data"
        elif tracked:
            answer = "No material findings yet — sync filings or run analysis."
        else:
            answer = "Add a ticker to start your brief."

        if (gate_withheld or pipeline_failed) and answer_posture is not None:
            answer = f"{answer} {_coverage_sentence(gate_withheld, pipeline_failed)}"
```

> `answer_posture` is retained here only so W1 and W3 land cleanly; **W6-C12 deletes the whole posture chain** (Decision D3 = remove end to end). Do not delete it early — an intermediate commit that drops it while `BriefView` still declares it will fail `tests/test_digest.py`.

**Required additional test** (add to the W1 test table):

| id | file | name | asserts |
|---|---|---|---|
| W1-T2a | `tests/test_presentation.py` | `test_critical_finding_outranks_a_pipeline_failure_in_the_headline` | Build a brief with one `CRITICAL` published finding **and** one `pipeline_failed` entry. Assert `brief.answer` **starts with** `"A tracked company needs a critical review."`, **contains** `"could not be analyzed"`, and does **not** start with `"1 filing could not be analyzed"`. Fails before this amendment. |

**QA-1 step 2 changes accordingly:** with a critical finding also present, the headline must lead with the finding and carry the coverage sentence second. With no findings, the QA-1 wording as written still applies.

---

> **D1 — Render the verification gate, or delete the dead field? (blocks W2.)**
> `AGENTS.md` / `CLAUDE.md` §10 currently scopes the filing UI to "only the tool count, compact trace, dropped codes, and conditional download". `PresentationService.filing()` already builds a complete `VerificationView`, ships it on `FilingDetailView.verification`, and `web/src/types.ts` types it — and **no JSX in `web/src` ever reads it**. `VerificationCheckView.detail` is declared and permanently `None`.
> **Option A (recommended): RENDER.** Ship W2 and reword §10 in both mirrors (exact wording in W2 doc updates).
> **Option B: DELETE.** Remove `VerificationView`/`VerificationCheckView` from `FilingDetailView` (models.py:144), delete the roll-up construction (service.py:212-234), delete `Verification` from `web/src/types.ts`, and update `tests/test_web.py:189` (`assert filing.json()["verification"] is not None`). §10 stays as written.
> Either way the field must not remain half-wired — that is exactly the dormant scaffolding the project-vision section forbids.

> **D2 — Ship the bundled demo dataset in hosted mode? (blocks W4-C7.)**
> `src/finwatch/web/app.py:616` forces `demo = demo and not remote` with a `LOW-6` comment, and `tests/test_web_security.py:202-210` asserts that behavior. Consequence today: the hosted "Explore the sample brief" button navigates to the identical empty page. W4 proposes reversing it. Safety argument: the demo DB is a throwaway `:memory:` build of bundled public SEC fixtures, closed at end of request, never written by the read endpoints, projected as `LOCAL_USER_ID` so no participant scope applies, and hosted `/api/*` still requires a valid session cookie. **If the answer is no, drop W4-C7 and W4's `test_remote_sample_brief_serves_only_bundled_public_data`, and instead delete the sample-brief button and the `AnalysisPanel.tsx:22` claim that it works.**

> **D3 — Remove the posture chain end to end? (blocks W6-C9 and W6-C12.)**
> `BriefView.answer_posture` crosses the API boundary; its only React consumer is a variable feeding a CSS class that no longer exists. `PosturePill.tsx` renders the raw token `critical_review` and is imported only by its own test. §1 says the launch UI never emits a P3 posture. Removing it changes the `/api/brief` response shape. **If the answer is no,** W1-C6 and W3-C5 keep their `answer_posture` assignments as written and W6 ships without C9's type-level deletions and without C12.

> **D4 — Brief headline ordering.** W1 preserves today's behavior exactly: gate-withheld and pipeline-failed outrank a critical finding. So if one issuer has a genuine going-concern finding and another failed to download, the headline reports the download failure. This is unchanged, but becomes more visible. **Confirm this is intended, or schedule a separate change.** Do not "fix" it inside W1.

> **D5 — Presentation-only fourth metric state.** W3 adds `"withheld"` to `MetricState` in `presentation/models.py`. The envelope in `metrics/envelope.py` stays three-valued and is not touched. This requires an §8 doc amendment. **Confirm** the four-state presentation vocabulary is acceptable before shipping W3.

---

## 2. W1 — Stop asserting false things about system state

**Commit:** `fix: name pipeline failures and gate-removed findings honestly`

### 2.1 Goal

Every launch surface names the actual system state instead of defaulting to publication-gate language.

- A download/parse/metrics/orchestrator crash, or a run that produced no `VerificationReport`, is presented as "analysis did not complete" (`withheld_kind="pipeline_failed"`), never "held back by the publication gate".
- A run whose candidate findings were **all** pruned is presented as "proposed changes removed by the evidence gate" (`outcome="findings_dropped"`) instead of the false all-clear "Nothing important changed".
- `BriefPeriodView` keeps `analyzed_filings` and gains `published_filings` (attempts that actually cleared the gate) and `withheld_filings`.
- The companies list reports the newest **supported** 10-K/10-Q/8-K, not the newest filing of any form, and stops claiming it was "read".
- English pluralization is fixed everywhere a count is rendered.
- `digest/render.py` mirrors every brief-level field off the same `BriefView`.
- `verify/presentation.py` gains four additive invariants.

### 2.2 Why it matters

Today an EDGAR 403 renders as "Held back by the publication gate … did not clear verification" — telling the user our verifier caught a problem in Apple's 10-Q when we never downloaded it. That manufactures false negative signal about a real issuer and destroys the credibility of genuine gate refusals, because the user cannot tell the two apart. `tests/test_run.py:421` and `:481` currently *assert* this bug. Symmetrically, when the gate does its most valuable work — deleting every proposed finding because a quote was not exact — the largest type on the page reads "Nothing important changed". And "Analyzed 5 through the gate" next to "1 filing withheld" contradicts itself on one screen.

### 2.3 Changes

#### W1-C1 · `src/finwatch/presentation/models.py`

**Anchor:** module-level `Literal` aliases (line 15, `MetricState = ...`), `class FilingDigestEntry` (36-44), `class BriefPeriodView` (63-66), `class BriefView` (69-81), `class CompanyRowView` (152-156).

**Current:**
```python
MetricState = Literal["computed", "unavailable", "not_applicable"]
...
class FilingDigestEntry(BaseModel):
    ...
    findings: list[FindingView] = Field(default_factory=list, max_length=3)
    withheld: bool = False
    withheld_reason: str | None = None
...
class BriefPeriodView(BaseModel):
    covered: str
    filings_in_window: int
    analyzed_filings: int
...
class CompanyRowView(BaseModel):
    ticker: str
    cik: str
    last_filing: str | None = None
    compressed_verified_read: str | None = None
```

**Change.** Declare `WithheldKind` and `FilingOutcome` directly under `MetricState`. `FilingDigestEntry` gains `withheld_kind`, `outcome` and `dropped_finding_count`. `outcome` defaults to `not_analyzed` so an entry built without it is never treated as published. `dropped_finding_count` is `ge=0`, so a negative count is unconstructible. `BriefPeriodView` gains `published_filings` and `withheld_filings`, both defaulting to 0 so the direct construction in `tests/test_digest.py:112-115` keeps compiling. `BriefView` gains `gate_removed_filings`. `CompanyRowView.last_filing` is **renamed** `newest_supported_filing` — the name is part of the lie, so it is renamed, not aliased. Verified by grep: the only other references are `service.py:455`, `web/src/types.ts:38`, `web/src/pages/CompaniesPage.tsx:27`, all updated here.

**New code:**
```python
MetricState = Literal["computed", "unavailable", "not_applicable"]
# "gate" = a deterministic publication refusal; "pipeline_failed" = the attempt never
# produced a verification result at all. These two must never share user-facing copy.
WithheldKind = Literal["gate", "pipeline_failed"]
FilingOutcome = Literal[
    "published",
    "no_findings",
    "findings_dropped",
    "withheld_gate",
    "pipeline_failed",
    "not_analyzed",
]


class FilingDigestEntry(BaseModel):
    accession: str = Field(min_length=1, max_length=32)
    ticker: str = Field(min_length=1, max_length=16)
    form: str = Field(min_length=1, max_length=16)
    filed: str = Field(min_length=1, max_length=32)
    edgar_url: str = Field(min_length=1, max_length=500)
    findings: list[FindingView] = Field(default_factory=list, max_length=3)
    withheld: bool = False
    withheld_reason: str | None = None
    withheld_kind: WithheldKind | None = None
    outcome: FilingOutcome = "not_analyzed"
    dropped_finding_count: int = Field(default=0, ge=0)


class BriefPeriodView(BaseModel):
    covered: str
    filings_in_window: int
    # Attempts with a persisted analysis row, whether or not they cleared the gate.
    analyzed_filings: int
    # Attempts that cleared the publication gate: published findings, every candidate
    # dropped, or a genuinely routine filing. All three publish deterministic metrics.
    published_filings: int = 0
    # Attempts refused by the gate plus attempts ended by a pipeline failure.
    withheld_filings: int = 0


class BriefView(BaseModel):
    period: BriefPeriodView
    tracked_tickers: list[str] = Field(default_factory=list)
    answer: str
    answer_posture: Posture | None = None
    filings: list[FilingDigestEntry] = Field(default_factory=list)
    gate_removed_filings: list[FilingDigestEntry] = Field(default_factory=list)
    verified_numbers: list[IssuerMetricsView] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    boring_filings: str | None = None
    withheld_filings: list[FilingDigestEntry] = Field(default_factory=list)
    tracked_but_unanalyzed: bool = False
    disclaimer: str = DISCLAIMER
    sample_data: bool = False


class CompanyRowView(BaseModel):
    ticker: str
    cik: str
    newest_supported_filing: str | None = None
    compressed_verified_read: str | None = None
```

#### W1-C2 · `src/finwatch/presentation/projection.py`

**Anchor:** `_REQUIRED_PUBLICATION_CHECKS = frozenset({"V1", "V4", "V5"})` (line 11); `FilingProjection` fields (14-26); the `withheld = filing.status in {"failed", "analyzed"} ...` block (98-105); the `return FilingProjection(` call (111-123).

**Current:**
```python
    withheld: bool
    withheld_reason: str | None = None
...
    withheld = filing.status in {"failed", "analyzed"} or bool(
        analysis_present and not llm_output_allowed
    )
    withheld_reason = (
        "LLM-derived analysis withheld because deterministic verification did not pass."
        if withheld and analysis_present
        else None
    )
```

**Change.** `filing.status == "failed"` is set only by `pipeline/run.py:169, :184, :208` (missing primary-doc URL, EDGAR fetch failure, any orchestrator exception) and by `orchestrator.py:137-143` when `report is None`. `"analyzed"` is set only when a report exists with verdict FAIL. So `status == "failed"` is a clean, complete discriminator — no stage-table lookup needed. Introduce module-level reason constants so exactly one string exists per cause, add `withheld_kind`, and stop gating the reason on `analysis_present` (that gap produced `withheld=True` / `reason=None` and forced both surfaces into gate-worded fallbacks). Both reasons are fixed user-safe constants.

Note the wording deliberately does **not** claim "no filing content was interpreted" — in the crashed-verifier case a P1 row exists (`tests/test_run.py:414` proves it), so claiming otherwise would be a fresh false assertion.

**New code:**
```python
_REQUIRED_PUBLICATION_CHECKS = frozenset({"V1", "V4", "V5"})

# Exactly one user-facing string per withholding cause. Both are fixed constants: no
# stage error, provider exception, or persisted diagnostic ever reaches a user here.
GATE_WITHHELD_REASON = (
    "LLM-derived analysis withheld because deterministic verification did not pass."
)
PIPELINE_FAILED_REASON = (
    "Analysis did not complete for this filing, so nothing was published. "
    "No deterministic verification result was recorded for this attempt."
)


@dataclass
class FilingProjection:
    filing: Filing
    company: Company | None
    p1: P1Output | None
    analysis_present: bool
    llm_output_allowed: bool
    withheld: bool
    withheld_kind: str | None = None
    withheld_reason: str | None = None
    data_quality: list[tuple[str, str]] = field(default_factory=list)
    p1_analysis: Analysis | None = None
    trace_analysis: Analysis | None = None
    trace: HarnessTrace | None = None
```

Inside `load_filing_projection`, replacing lines 98-105:
```python
    # A "failed" filing never produced a verification result: run.py marks download,
    # parse, metrics and orchestrator crashes that way, and orchestrator.py marks a run
    # with no VerificationReport that way. Calling that a gate refusal would blame the
    # verifier for an EDGAR outage.
    pipeline_failed = filing.status == "failed"
    withheld = (
        pipeline_failed
        or filing.status == "analyzed"
        or bool(analysis_present and not llm_output_allowed)
    )
    withheld_kind = (
        "pipeline_failed" if pipeline_failed else "gate" if withheld else None
    )
    withheld_reason = (
        PIPELINE_FAILED_REASON
        if withheld_kind == "pipeline_failed"
        else GATE_WITHHELD_REASON
        if withheld_kind == "gate"
        else None
    )
```

And in the `FilingProjection(...)` construction, directly after `withheld=withheld,`:
```python
        withheld_kind=withheld_kind,
```

#### W1-C3 · `src/finwatch/presentation/canonical.py` *(trust-critical)*

**Anchor:** import at line 11; `_WITHHELD` (16-18); `_base_entry` (38-48); `_withhold` (51-53); the tail of `build_filing_entry` from `findings.sort(...)` (170-174).

**Current:**
```python
from finwatch.presentation.projection import FilingProjection
...
_WITHHELD = (
    "LLM-derived analysis withheld because its displayed findings could not be verified exactly."
)
...
def _base_entry(view: FilingProjection, *, withheld: bool | None = None) -> FilingDigestEntry:
    url, _ = _edgar_url(view.filing)
    return FilingDigestEntry(
        ...
        withheld=view.withheld if withheld is None else withheld,
        withheld_reason=view.withheld_reason,
    )


def _withhold(view: FilingProjection) -> FilingDigestEntry:
    entry = _base_entry(view, withheld=True)
    return entry.model_copy(update={"findings": [], "withheld_reason": _WITHHELD})
...
    findings.sort(key=lambda row: (_SEVERITY_RANK[row.severity], row.finding_id))
    entry = entry.model_copy(update={"findings": findings[:3]})
    if verify_filing_entry(entry, sections):
        return _withhold(view)
    return entry
```

**Change.** Carry the discriminator and the drop count into the one DTO both surfaces consume. `_withhold` must stop overwriting a pipeline-failure reason with gate wording. `dropped_finding_count` is the number of candidates the harness/compiler already pruned for this attempt (`HarnessTrace.dropped_findings`, populated at `harness.py:1050` and `:1083`, verified disjoint from `output.findings`) plus any candidate this projection itself refused.

**THIS DOES NOT WEAKEN THE GATE:** every branch that previously withheld still withholds, with identical findings suppression, and a `verify_filing_entry` failure still routes through `_withhold`. Nothing is edited to make a check pass.

**New code:**
```python
from finwatch.presentation.projection import PIPELINE_FAILED_REASON, FilingProjection

_WITHHELD = (
    "LLM-derived analysis withheld because its displayed findings could not be verified exactly."
)


def _dropped_count(view: FilingProjection) -> int:
    """Findings the harness/compiler already pruned for this exact attempt."""
    return len(view.trace.dropped_findings) if view.trace is not None else 0


def _base_entry(view: FilingProjection, *, withheld: bool | None = None) -> FilingDigestEntry:
    url, _ = _edgar_url(view.filing)
    held = view.withheld if withheld is None else withheld
    # An entry withheld by this module (bad identity, unverifiable display text) is a
    # gate refusal even when the projection saw no explicit kind.
    kind = (view.withheld_kind or "gate") if held else None
    return FilingDigestEntry(
        accession=view.filing.accession_number,
        ticker=view.ticker,
        form=view.filing.form_type,
        filed=_date(view.filing.filed_at),
        edgar_url=url,
        withheld=held,
        withheld_reason=view.withheld_reason if held else None,
        withheld_kind=kind,
        outcome=(
            "not_analyzed"
            if kind is None
            else "pipeline_failed"
            if kind == "pipeline_failed"
            else "withheld_gate"
        ),
        dropped_finding_count=_dropped_count(view),
    )


def _withhold(view: FilingProjection) -> FilingDigestEntry:
    entry = _base_entry(view, withheld=True)
    reason = PIPELINE_FAILED_REASON if entry.withheld_kind == "pipeline_failed" else _WITHHELD
    return entry.model_copy(update={"findings": [], "withheld_reason": reason})
```

Replacing lines 170-174 at the tail of `build_filing_entry`:
```python
    findings.sort(key=lambda row: (_SEVERITY_RANK[row.severity], row.finding_id))
    dropped = entry.dropped_finding_count + (len(view.p1.findings) - len(findings))
    entry = entry.model_copy(
        update={
            "findings": findings[:3],
            "dropped_finding_count": dropped,
            # A filing with no surviving finding but a nonzero drop count is not boring:
            # the gate deleted every candidate. Saying "nothing changed" there would be
            # a false all-clear.
            "outcome": (
                "published" if findings else "findings_dropped" if dropped else "no_findings"
            ),
        }
    )
    if verify_filing_entry(entry, sections):
        return _withhold(view)
    return entry
```

#### W1-C4 · `src/finwatch/verify/presentation.py` *(TRUST-CRITICAL)*

**Anchor:** `verify_filing_entry`, from `errors: list[str] = []` through `errors.append("more than three findings")` (18-22), immediately before the `parsed_url = urlsplit(...)` check.

**Current:**
```python
    errors: list[str] = []
    if entry.withheld and entry.findings:
        errors.append("withheld entry contains findings")
    if len(entry.findings) > 3:
        errors.append("more than three findings")
```

**Change.** The new `outcome`/`withheld_kind` fields are user-facing claims about system state, so the final-DTO verifier must prove they agree with the content actually in the DTO — otherwise a projection bug could ship an entry labelled `published` with zero findings, or labelled `no_findings` while carrying findings, or claim a gate refusal for a pipeline failure. `dropped_finding_count` needs no check (pydantic `ge=0` makes a negative unconstructible). **Strictly additive: nothing internally consistent that previously passed starts failing, and no existing check is removed or loosened.**

**New code:**
```python
    errors: list[str] = []
    if entry.withheld and entry.findings:
        errors.append("withheld entry contains findings")
    if len(entry.findings) > 3:
        errors.append("more than three findings")
    # The outcome and withheld kind are user-facing claims about system state. They must
    # agree with the content in this exact DTO, or the UI can describe a pipeline failure
    # as a gate refusal (or describe suppressed content as an all-clear).
    if entry.withheld != (entry.withheld_kind is not None):
        errors.append("withheld flag disagrees with the withheld kind")
    if entry.withheld != (entry.outcome in {"withheld_gate", "pipeline_failed"}):
        errors.append("withheld flag disagrees with the published outcome")
    if entry.findings and entry.outcome != "published":
        errors.append("findings present under a non-published outcome")
    if entry.outcome == "published" and not entry.findings:
        errors.append("published outcome without findings")
```

#### W1-C5 · `src/finwatch/presentation/formatting.py`

**Anchor:** new module-level helper appended at end of file (file is 87 lines, ending `return _num(result.value) if result.value is not None else "computed"`).

**Current:** no plural helper exists. `service.py:157` hand-rolls `"{'s' if count != 1 else ''}"`, `service.py:163,176` hard-code `"filing(s)"`, and `service.py:166` hard-codes the unconditional plural `"routine filings"`, so a single boring filing renders as "1 routine filings".

**New code:**
```python
def plural_count(value: int, noun: str) -> str:
    """Render a count with an English plural so no surface prints "1 filings"."""
    return f"{value} {noun}" if value == 1 else f"{value} {noun}s"
```

#### W1-C6 · `src/finwatch/presentation/service.py` — `brief()`

**Anchor:** `PresentationService.brief` starts at line 129; replace the body from `tracked = self.repo.list_tracked_companies(self.user_id)` (136) through the closing `)` of `return BriefView(...)` (203). Also line 14.

**Change.** Line 14 becomes `from finwatch.presentation.formatting import format_metric_value, plural_count` (`BriefPeriodView`/`BriefView` are already imported at 16-17). Bucket entries off the explicit `outcome` discriminator instead of re-deriving state, split the withheld headline into gate vs pipeline copy, add the `gate_removed` bucket and its open question, report honest period counts, and use `plural_count` everywhere. **Headline ordering IS changed — see W1-C6a below, which supersedes the ordering in this change's code block.** Decision D4 was answered "findings first". All lines stay within ruff's 100-char limit.

**New code:**
```python
        tracked = self.repo.list_tracked_companies(self.user_id)
        views = self._views(since, until)
        analyzed = [view for view in views if view.analysis_present]
        entries = [build_filing_entry(self.repo, view) for view in views]
        published = [entry for entry in entries if entry.outcome == "published"]
        gate_withheld = [entry for entry in entries if entry.outcome == "withheld_gate"]
        pipeline_failed = [entry for entry in entries if entry.outcome == "pipeline_failed"]
        withheld = gate_withheld + pipeline_failed
        gate_removed = [entry for entry in entries if entry.outcome == "findings_dropped"]
        boring = [entry for entry in entries if entry.outcome == "no_findings"]
        # Only these three buckets actually cleared the publication gate.
        cleared_gate = len(published) + len(gate_removed) + len(boring)

        tracked_tickers = sorted(company.ticker for company in tracked)
        severe = any(
            any(finding.severity in {"CRITICAL", "HIGH"} for finding in entry.findings)
            for entry in published
        )

        answer_posture = None
        if gate_withheld:
            answer = (
                f"{plural_count(len(gate_withheld), 'filing')} withheld — "
                "could not be verified."
            )
            answer_posture = "risk_review"
        elif pipeline_failed:
            answer = (
                f"{plural_count(len(pipeline_failed), 'filing')} could not be analyzed — "
                "the pipeline did not complete."
            )
            answer_posture = "insufficient_data"
        elif severe:
            answer = "A tracked company needs a critical review."
            answer_posture = "critical_review"
        elif published:
            answer = f"Important changes found in {plural_count(len(published), 'filing')}."
            answer_posture = "risk_review"
        elif gate_removed:
            answer = (
                f"Every proposed change in {plural_count(len(gate_removed), 'filing')} "
                "failed the evidence gate. Verified numbers still published."
            )
            answer_posture = "risk_review"
        elif analyzed:
            answer = (
                "Nothing important changed. "
                f"{plural_count(len(boring), 'routine filing')} reviewed."
            )
            answer_posture = "monitor"
        elif tracked:
            answer = "No material findings yet — sync filings or run analysis."
        else:
            answer = "Add a ticker to start your brief."

        boring_line = None
        if boring:
            listing = ", ".join(f"{entry.ticker} {entry.form}" for entry in boring)
            boring_line = (
                f"{plural_count(len(boring), 'routine filing')} with no material findings "
                f"({listing})."
            )

        questions = [
            f"{view.ticker}: a deterministic data-quality check needs review."
            for view in analyzed
            if view.data_quality
        ]
        questions.extend(
            f"{entry.ticker}: automated verification withheld this filing."
            for entry in gate_withheld
        )
        questions.extend(
            f"{entry.ticker}: analysis did not complete, so this filing was never published."
            for entry in pipeline_failed
        )
        questions.extend(
            f"{entry.ticker}: every proposed change failed the evidence gate."
            for entry in gate_removed
        )
        return BriefView(
            period=BriefPeriodView(
                covered=f"{since or 'inception'} → {until or 'now'}",
                filings_in_window=len(views),
                analyzed_filings=len(analyzed),
                published_filings=cleared_gate,
                withheld_filings=len(withheld),
            ),
            tracked_tickers=tracked_tickers,
            answer=answer,
            answer_posture=answer_posture,
            filings=published,
            gate_removed_filings=gate_removed,
            verified_numbers=[self._issuer_metrics(c) for c in tracked],
            open_questions=questions,
            boring_filings=boring_line,
            withheld_filings=withheld,
            tracked_but_unanalyzed=bool(tracked and not analyzed),
            sample_data=sample_data,
        )
```

#### W1-C7 · `src/finwatch/presentation/service.py` — `companies()`

> **Merged:** W3-C6 and W5-C11 also rewrite this method. Apply W1-C7 now; W5-C11 carries the final merged body.

**Anchor:** `PresentationService.companies` (417-459): the loop head at 420-421 and the `CompanyRowView(...)` construction at 452-457.

**Current:**
```python
        for company in self.repo.list_tracked_companies(self.user_id):
            filings = self.repo.list_filings(company.cik)
            latest = filings[0] if filings else None
...
                CompanyRowView(
                    ticker=company.ticker,
                    cik=company.cik,
                    last_filing=_date(latest.filed_at) if latest else None,
                    compressed_verified_read=compressed,
                )
```

**Change.** `repo.list_filings(cik)` (`repositories.py:310-317`) is a bare `SELECT * FROM filings WHERE cik = ? ORDER BY filed_at DESC` with no form filter, and ingest stores Form 4, S-8, DEF 14A and 20-F. Filter to the three supported base forms — the same predicate `_views` applies at `service.py:48` — and populate the renamed field. `base_form` is already imported at `service.py:12`. The compressed-verified-read block between them (422-450) is untouched here.

**New code** (replacing 420-421):
```python
            # The newest filing of *any* form is not what this row means: ingest also
            # stores Form 4, S-8, DEF 14A and 20-F, none of which this product reads.
            supported = [
                filing
                for filing in self.repo.list_filings(company.cik)
                if base_form(filing.form_type) in {"10-K", "10-Q", "8-K"}
            ]
            latest = supported[0] if supported else None
```

(replacing 452-457):
```python
                CompanyRowView(
                    ticker=company.ticker,
                    cik=company.cik,
                    newest_supported_filing=_date(latest.filed_at) if latest else None,
                    compressed_verified_read=compressed,
                )
```

#### W1-C8 · `src/finwatch/digest/render.py`

**Anchor:** import at line 16; `_header` (53-67); `_withheld_section` (70-84); the `lines.extend(_withheld_section(...))` / `lines.extend(_findings_section(...))` pair (175-176).

**Current:**
```python
        (
            f"- **Filings in window:** {brief.period.filings_in_window} "
            f"· **Analyzed:** {brief.period.analyzed_filings}"
        ),
...
def _withheld_section(entries: list[FilingDigestEntry]) -> list[str]:
    if not entries:
        return []
    out = ["## Withheld analyses", ""]
    for entry in entries:
        reason = entry.withheld_reason or (
            "LLM-derived analysis withheld because deterministic verification did not pass."
        )
...
    lines.extend(_withheld_section(brief.withheld_filings))
    lines.extend(_findings_section(brief.filings))
```

**Change.** Mirror every brief-level change. This module still reloads nothing (`render_digest` still builds the brief only through `PresentationService(repo).brief(...)`). Four edits: extend line 16 to `from finwatch.presentation.formatting import format_metric_value, plural_count` and add `from finwatch.presentation.projection import GATE_WITHHELD_REASON, PIPELINE_FAILED_REASON` (isort order: `formatting`, `models`, `projection`); `_header` reports honest counts labelled **Analysis on file** because `analyzed_filings` counts attempts with a persisted analysis row, not attempts started; `_withheld_section` splits by `withheld_kind`; `render_brief_markdown` emits the gate-removed section after findings.

**New code:**
```python
def _header(brief: BriefView) -> list[str]:
    tracked = ", ".join(brief.tracked_tickers) or "none"
    return [
        "# finwatch digest",
        "",
        f"> {_markdown_text(brief.answer)}",
        "",
        f"- **Period covered:** {_markdown_text(brief.period.covered)}",
        f"- **Holdings tracked:** {_markdown_text(tracked)}",
        (
            f"- **Filings in window:** {brief.period.filings_in_window} "
            f"· **Analysis on file:** {brief.period.analyzed_filings} "
            f"· **Published:** {brief.period.published_filings} "
            f"· **Withheld:** {brief.period.withheld_filings}"
        ),
        "",
    ]


def _entry_lines(entries: list[FilingDigestEntry], fallback: str) -> list[str]:
    return [
        f"- [{_markdown_text(entry.ticker)} — {_markdown_text(entry.form)} filed "
        f"{_markdown_text(entry.filed)}]({entry.edgar_url}) — "
        f"{_markdown_text(entry.withheld_reason or fallback)}"
        for entry in entries
    ]


def _withheld_section(entries: list[FilingDigestEntry]) -> list[str]:
    """Separate a deterministic gate refusal from a run that never reached a verdict."""
    gate = [entry for entry in entries if entry.withheld_kind != "pipeline_failed"]
    failed = [entry for entry in entries if entry.withheld_kind == "pipeline_failed"]
    out: list[str] = []
    if gate:
        out.extend(["## Withheld analyses", ""])
        out.extend(_entry_lines(gate, GATE_WITHHELD_REASON))
        out.append("")
    if failed:
        out.extend(["## Filings that could not be analyzed", ""])
        out.extend(_entry_lines(failed, PIPELINE_FAILED_REASON))
        out.append("")
    return out


def _gate_removed_section(entries: list[FilingDigestEntry]) -> list[str]:
    if not entries:
        return []
    out = ["## Proposed changes removed by the evidence gate", ""]
    for entry in entries:
        out.append(
            f"- [{_markdown_text(entry.ticker)} — {_markdown_text(entry.form)} filed "
            f"{_markdown_text(entry.filed)}]({entry.edgar_url}) — "
            f"{plural_count(entry.dropped_finding_count, 'proposed change')} failed a "
            "deterministic evidence check; verified numbers are unaffected."
        )
    out.append("")
    return out
```

In `render_brief_markdown`, replacing lines 175-176:
```python
    lines.extend(_withheld_section(brief.withheld_filings))
    lines.extend(_findings_section(brief.filings))
    lines.extend(_gate_removed_section(brief.gate_removed_filings))
```

#### W1-C9 · `web/src/types.ts`

**Anchor:** `FilingDigestEntry` (line 8), `Brief` (11), `TrackedCompany` (38); new unions beside `MetricState` (3).

**Change.** Mirror the DTO exactly. New fields are **required** (not optional) so the compiler forces every construction site and test fixture to state the filing's real outcome.

**New code:**
```typescript
export type WithheldKind = "gate" | "pipeline_failed";
export type FilingOutcome = "published" | "no_findings" | "findings_dropped" | "withheld_gate" | "pipeline_failed" | "not_analyzed";

export interface FilingDigestEntry { accession: string; ticker: string; form: string; filed: string; edgar_url: string; findings: Finding[]; withheld: boolean; withheld_reason: string | null; withheld_kind: WithheldKind | null; outcome: FilingOutcome; dropped_finding_count: number }
export interface Brief { period: { covered: string; filings_in_window: number; analyzed_filings: number; published_filings: number; withheld_filings: number }; tracked_tickers: string[]; answer: string; answer_posture: Posture | null; filings: FilingDigestEntry[]; gate_removed_filings: FilingDigestEntry[]; verified_numbers: IssuerMetrics[]; open_questions: string[]; boring_filings: string | null; withheld_filings: FilingDigestEntry[]; tracked_but_unanalyzed: boolean; disclaimer: string; sample_data: boolean }
export interface TrackedCompany { ticker: string; cik: string; newest_supported_filing: string | null; compressed_verified_read: string | null }
```

#### W1-C10 · `web/src/components/FilingItemCard.tsx`

**Anchor:** whole component (13 lines); state line 8 and the ternary body on line 11.

**Current:**
```tsx
  const withheld = withholdFindings || filing.withheld || Boolean(filing.withheld_reason);
  return <article className="filing-card">
    ...
    {withheld ? <div className="withheld-copy compact"><strong>Held back by the publication gate</strong><p>{filing.withheld_reason ?? "No model-authored finding is shown because this filing did not clear verification."}</p></div> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected.</p>}
```

**Change.** Render three distinct states: pipeline failure, gate refusal, and gate-removed proposals. All text stays escaped React text.

**New code:**
```tsx
import { Link, useLocation } from "react-router-dom";
import type { FilingDigestEntry } from "../types";
import { FindingList } from "./FindingList";

export function FilingItemCard({ filing, withholdFindings = false }: { filing: FilingDigestEntry; withholdFindings?: boolean }) {
  const location = useLocation();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const withheld = withholdFindings || filing.withheld || Boolean(filing.withheld_reason);
  const pipelineFailed = filing.withheld_kind === "pipeline_failed" || filing.outcome === "pipeline_failed";
  const dropped = filing.dropped_finding_count;
  return <article className="filing-card">
    <div className="filing-heading"><div className="filing-identity"><Link className="filing-link" to={`/filings/${filing.accession}${demo ? "?demo=1" : ""}`}><strong>{filing.ticker}</strong><span aria-hidden="true">→</span></Link><span className="form-badge">{filing.form}</span><span className="mono muted">Filed {filing.filed}</span></div></div>
    {withheld
      ? pipelineFailed
        ? <div className="withheld-copy compact neutral"><strong>Analysis did not complete</strong><p>{filing.withheld_reason ?? "The pipeline stopped before this filing was published. No deterministic verification result was recorded."}</p></div>
        : <div className="withheld-copy compact"><strong>Held back by the publication gate</strong><p>{filing.withheld_reason ?? "No model-authored finding is shown because this filing did not clear verification."}</p></div>
      : filing.findings.length
        ? <FindingList findings={filing.findings} />
        : filing.outcome === "findings_dropped"
          ? <p className="empty-line">{dropped === 1 ? "1 proposed change was" : `${dropped} proposed changes were`} removed by the evidence gate. Verified numbers are unaffected.</p>
          : <p className="empty-line">No evidence-backed changes were selected.</p>}
  </article>;
}
```

#### W1-C11 · `web/src/pages/BriefPage.tsx`

**Anchor:** `const answerTone = ...` (32); the `<div className="brief-stats">` tiles (39-44), specifically the `Analyzed` tile on 42; the single withheld section (50).

**Current:**
```tsx
        <div><span>Analyzed</span><strong>{brief.period.analyzed_filings}</strong><small>through the gate</small></div>
...
    {brief.withheld_filings.length > 0 && <section className="section withheld-section"><SectionHeader index="Held back" title="Withheld analyses" /><p className="metric-caption">The gate refused to publish model-authored content from these filings. This is a deliberate trust outcome.</p>{brief.withheld_filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} withholdFindings />)}</section>}
```

**Change.** The "Analyzed N through the gate" tile counts attempts that FAILED the gate. Replace with the published count and surface the withheld count in its subline, keeping exactly four tiles so the `.brief-stats` grid (`global.css:419-423` `1fr 1fr`; `:1491-1493` `repeat(4, 1fr)`) is unchanged. Split the withheld section, add the gate-removed section. Derive the two buckets immediately after line 32.

**New code:**
```tsx
  const gateWithheld = brief.withheld_filings.filter(filing => filing.withheld_kind !== "pipeline_failed");
  const pipelineFailed = brief.withheld_filings.filter(filing => filing.withheld_kind === "pipeline_failed");
```

Replacing the "Analyzed" tile on line 42:
```tsx
        <div><span>Published</span><strong>{brief.period.published_filings}</strong><small>{brief.period.withheld_filings > 0 ? `${brief.period.withheld_filings} held back` : "cleared the gate"}</small></div>
```

Replacing the single withheld section on line 50:
```tsx
    {gateWithheld.length > 0 && <section className="section withheld-section"><SectionHeader index="Held back" title="Withheld analyses" /><p className="metric-caption">The gate refused to publish model-authored content from these filings. This is a deliberate trust outcome.</p>{gateWithheld.map(filing => <FilingItemCard key={filing.accession} filing={filing} withholdFindings />)}</section>}
    {pipelineFailed.length > 0 && <section className="section not-analyzed-section"><SectionHeader index="Not analyzed" title="Filings that could not be analyzed" /><p className="metric-caption">The pipeline stopped before these filings were published. No deterministic verification result was recorded — this is not a verification outcome.</p>{pipelineFailed.map(filing => <FilingItemCard key={filing.accession} filing={filing} withholdFindings />)}</section>}
    {brief.gate_removed_filings.length > 0 && <section className="section"><SectionHeader index="Removed" title="Proposed changes removed by the evidence gate" /><p className="metric-caption">The model proposed changes for these filings and every one failed a deterministic evidence check, so none are shown. Verified numbers below are unaffected.</p>{brief.gate_removed_filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} />)}</section>}
```

#### W1-C12 · `web/src/pages/FilingPage.tsx`

**Anchor:** `const withheldReason = ...` (59); the `{!research && withheldReason && ...}` banner (77); the what-changed body (82).

**Current:**
```tsx
  const withheldReason = detail.withheld_reason ?? filing.withheld_reason ?? (filing.withheld ? "Findings withheld — could not be verified." : null);
...
    {!research && withheldReason && <section className="outcome-banner withheld"><span className="outcome-glyph" aria-hidden="true">!</span><div><p>Analysis held back</p><small>{withheldReason}</small></div></section>}
...
      {withheld ? <div className="withheld-copy"><strong>No model-authored finding is shown.</strong><p>{withheldReason ?? "This attempt did not clear the publication gate."}</p></div> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected. This is a legitimate routine result.</p>}
```

**Change.** Make the fallback, the no-research banner, and the withheld body kind-aware, and report gate-removed proposals instead of the routine-result line. Insert `const pipelineFailed = ...` immediately above line 59; line 60 (`const withheld = ...`) is unchanged. A pipeline-failed filing has no valid finalized trace, so `research` is null and it lands exactly on the banner at line 77.

**New code:**
```tsx
  const pipelineFailed = filing.withheld_kind === "pipeline_failed" || filing.outcome === "pipeline_failed";
  const withheldReason = detail.withheld_reason ?? filing.withheld_reason ?? (filing.withheld ? (pipelineFailed ? "Analysis did not complete for this filing, so nothing was published." : "Findings withheld — they could not be verified.") : null);
```

Replacing the no-research banner on line 77:
```tsx
    {!research && withheldReason && <section className={`outcome-banner ${pipelineFailed ? "not-analyzed" : "withheld"}`}><span className="outcome-glyph" aria-hidden="true">!</span><div><p>{pipelineFailed ? "Analysis did not complete" : "Analysis held back"}</p><small>{withheldReason}</small></div></section>}
```

Replacing the what-changed body on line 82:
```tsx
      {withheld ? <div className="withheld-copy"><strong>No model-authored finding is shown.</strong><p>{withheldReason ?? (pipelineFailed ? "The pipeline stopped before this filing was published." : "This attempt did not clear the publication gate.")}</p></div> : filing.findings.length ? <FindingList findings={filing.findings} /> : filing.outcome === "findings_dropped" ? <p className="empty-line">{filing.dropped_finding_count === 1 ? "1 proposed change was" : `${filing.dropped_finding_count} proposed changes were`} removed by the evidence gate. The verified numbers below are unaffected.</p> : <p className="empty-line">No evidence-backed changes were selected. This is a legitimate routine result.</p>}
```

#### W1-C13 · `web/src/pages/CompaniesPage.tsx`

**Anchor:** `<span className="surface-meta">Newest filing read per ticker</span>` (21) and, in `CompanyRow`, the `holding-last` div (27).

**Change.** Relabel to what the value actually is. "read" is false: nothing is downloaded or parsed at index time.

**New code:**
```tsx
<span className="surface-meta">Newest 10-K/10-Q/8-K indexed per ticker</span>
```
```tsx
<div className="holding-last"><span>Newest 10-K/10-Q/8-K</span><strong>{row.newest_supported_filing ?? "—"}</strong></div>
```

#### W1-C14 · `web/src/styles/global.css`

**Anchor:** after `.withheld-section { ... }` (531-534) and after `.outcome-banner.published { ... }` (786-789).

**Change.** Give the pipeline-failure state a visually distinct, non-alarming neutral treatment so it does not read as a verification warning. Reuse existing tokens only; add no new custom properties. `.withheld-copy.neutral` overrides the warn wash/border set by the shared rule at 500-506.

**New code:**
```css
.not-analyzed-section {
  padding-top: 28px;
  border-top: 2px solid var(--color-muted);
}

.withheld-copy.neutral {
  color: var(--color-body);
  background: var(--color-panel-alt);
  border-left-color: var(--color-muted);
}

.outcome-banner.not-analyzed {
  color: var(--color-muted);
  background: var(--color-panel-alt);
}
```

### 2.4 W1 tests

| ID | File | Test | Asserts | Fails before |
|---|---|---|---|---|
| W1-T1 | `tests/test_launch_projection.py` | `test_pipeline_failure_is_not_reported_as_a_gate_refusal` | `build_demo_db()`; `repo.upsert_filing(Filing(accession_number="0000320193-26-000099", cik="0000320193", form_type="8-K", filed_at="2026-05-01", status="failed"))`; no analysis rows; `entry = build_filing_entry(repo, load_filing_projection(repo, repo.get_filing(...)))`. Asserts `withheld is True`, `withheld_kind == "pipeline_failed"`, `outcome == "pipeline_failed"`, `withheld_reason == PIPELINE_FAILED_REASON`, `!= GATE_WITHHELD_REASON`. Add `from finwatch.db import Filing`. | ✅ |
| W1-T2 | `tests/test_presentation.py` | `test_gate_removed_findings_are_not_reported_as_nothing_changed` | `build_demo_db()`; `row = repo.latest_analysis("0000320193-24-000081", "P1_TRACE")`; `payload = json.loads(row.output_json)`; set **only** `payload["dropped_findings"] = [{"finding_id": "f1", "error_codes": ["QUOTE_NOT_EXACT"]}]` (preserving `trace_analysis_id`, `publication_outcome`, `terminal_reason`, `filing_snapshot`); `UPDATE analyses SET output_json = ? WHERE id = ?`; then `brief(since=DEMO_SINCE)`. Asserts `[e.accession for e in brief.gate_removed_filings] == ["0000320193-24-000081"]`, `dropped_finding_count == 1`, `outcome == "findings_dropped"`, and `brief.boring_filings == "1 routine filing with no material findings (AAPL 8-K)."` | ✅ |
| W1-T3 | `tests/test_presentation.py` | `test_period_counts_separate_published_from_withheld` | `build_demo_db()`; add `Filing(accession_number="0000866439-24-009999", cik="0000866439", form_type="10-Q", filed_at="2024-08-20", status="failed")`. Asserts `filings_in_window == 6`, `analyzed_filings == 5`, `published_filings == 5`, `withheld_filings == 1`, `answer == "1 filing could not be analyzed — the pipeline did not complete."`, `answer_posture == "insufficient_data"`. | ✅ |
| W1-T4 | `tests/test_presentation.py` | `test_companies_newest_filing_ignores_unsupported_forms` | Add `Filing(accession_number="0000320193-26-000444", cik="0000320193", form_type="4", filed_at="2026-06-30")`; `companies()`. Asserts AAPL's `newest_supported_filing == "2026-04-30"`. | ✅ |
| **W1-T5** | `tests/test_presentation_verifier.py` | `test_findings_under_a_non_published_outcome_fail` | **ADVERSARIAL, trust-critical.** Update `_fixture()` (lines 50-64) to construct the entry with `outcome="published"` — without it, `test_exact_quote_with_number_passes` (68-70) fails, which is itself evidence the invariant has teeth. Then `entry.model_copy(update={"outcome": "no_findings"})` must yield an error containing "non-published outcome", and `entry.model_copy(update={"outcome": "published", "findings": []})` must yield "published outcome without findings". | ✅ |
| **W1-T6** | `tests/test_presentation_verifier.py` | `test_withheld_flag_must_agree_with_kind_and_outcome` | **ADVERSARIAL, trust-critical.** `entry.model_copy(update={"withheld": True, "outcome": "withheld_gate", "findings": []})` (no `withheld_kind`) yields "withheld kind"; `entry.model_copy(update={"withheld_kind": "pipeline_failed"})` on the otherwise-unchanged non-withheld fixture also yields "withheld kind". | ✅ |
| W1-T7 | `web/src/components/components.test.tsx` | `names a pipeline failure as a pipeline failure, not a gate refusal` | Render `FilingItemCard` with `{ withheld: true, withheld_reason: null, withheld_kind: "pipeline_failed", outcome: "pipeline_failed", dropped_finding_count: 0, findings: [] }`. Asserts "Analysis did not complete" present, "Held back by the publication gate" null, `/verification/i` null. | ✅ |
| W1-T8 | `web/src/components/components.test.tsx` | `reports gate-removed proposals instead of silence` | Same shape with `withheld: false, outcome: "findings_dropped", dropped_finding_count: 2, findings: []`. Asserts `/2 proposed changes were removed by the evidence gate/` and NOT "No evidence-backed changes were selected." | ✅ |

**Existing expectations that MUST be updated in the same commit** (omitting any of these leaves the suite red):

- `web/src/components/components.test.tsx:57-58` — the fixture in `"withholds findings whenever a filing requires manual review"` gains `withheld_kind: "gate", outcome: "withheld_gate", dropped_finding_count: 0`. Its existing assertions must still pass unchanged.
- `web/src/pages/FilingPage.test.tsx` — the `detail()` factory `filing` object (28-37) gains `withheld_kind: null, outcome: "published", dropped_finding_count: 0`; the withheld override (108-112) gains `withheld_kind: "gate", outcome: "withheld_gate"`.
- `tests/test_run.py:421` and `:481` — both currently assert `"withheld — could not be verified"`. Both scenarios are pipeline failures (`assert repo.get_filing(ACCN).status == "failed"` at 412 and 480). Change both to assert `"could not be analyzed — the pipeline did not complete." in md` and add `assert "could not be verified" not in md`. **These two assertions are the pre-existing encoding of the bug and must flip.**
- `tests/test_presentation.py:22-24` — expected `boring_filings` becomes `"2 routine filings with no material findings (AAPL 8-K, AAPL 10-Q)."`; add `assert view.period.published_filings == 5` and `assert view.period.withheld_filings == 0` after line 17.
- `tests/test_digest.py:83` → `assert "2 routine filings with no material findings" in md`.
- `tests/test_digest.py:346` → `assert "1 routine filing with no material findings (QQQ 8-K)" in md`.

### 2.5 W1 acceptance criteria

1. A filing whose `filings.status` is `"failed"` produces `withheld_kind="pipeline_failed"`, `outcome="pipeline_failed"`, and copy that never contains "gate", "verification did not pass", or "could not be verified" on the brief card, the filing detail page, or in the Markdown digest.
2. A verified attempt that published zero findings while the finalized trace's `dropped_findings` is non-empty produces `outcome="findings_dropped"`, appears under `brief.gate_removed_filings`, is excluded from `boring_filings`, and never contributes to a "Nothing important changed" headline.
3. `published_filings` counts only attempts that cleared the gate; `withheld_filings` counts gate refusals plus pipeline failures; the two sum to at most `filings_in_window`.
4. No user-facing string renders "1 routine filings", "1 filings", or "filing(s)" in the brief, the digest Markdown, or the React surfaces (`cli.py:192` is operator tooling and out of scope).
5. `CompanyRowView.newest_supported_filing` ignores every form whose `base_form` is not 10-K/10-Q/8-K, and no surface claims that filing was "read".
6. `digest/render.py` still constructs the brief only through `PresentationService(repo).brief(...)` and reloads nothing.
7. **No fail-closed behavior weakened:** every branch that withheld before still withholds, `verify_filing_entry` gained four checks and lost none, a `verify_filing_entry` failure still routes through `_withhold`.
8. No raw stage error, provider exception, or persisted diagnostic reaches any new copy; no `dangerouslySetInnerHTML` introduced.
9. `uv run pytest -q` green; `cd web && npm run typecheck && npm test -- --run && npm run build` green; `uv run ruff check .` clean.
10. `diff AGENTS.md CLAUDE.md` empty; both changed in the same commit as the code.

### 2.6 W1 doc updates (both mirrors, same commit)

**§11 "One canonical presentation path", bullet list at lines 366-371.** Keep the first four bullets, then extend so the list reads:

```
- “AI-selected changes (evidence verified)” separates model judgment from deterministic evidence validation;
- exact quotations link to HTTPS SEC pages;
- verified numbers show state, formula version, effective date, and computation provenance;
- boring filings are a valid compact result;
- each `FilingDigestEntry` carries an explicit `outcome` (`published`, `no_findings`, `findings_dropped`, `withheld_gate`, `pipeline_failed`, `not_analyzed`) and a `dropped_finding_count`, so a run whose candidate findings were all pruned is never announced as “nothing important changed”;
- a pipeline failure (`withheld_kind = pipeline_failed`: missing document URL, fetch failure, parse/metrics/orchestrator crash, or a run with no verification report) is presented in its own bucket with its own fixed copy and is never described as a gate refusal or a verification outcome;
- brief period counts separate attempts with an analysis on file (`analyzed_filings`) from attempts that cleared the gate (`published_filings`) and attempts held back (`withheld_filings`);
- withheld filings never expose the failed LLM output.
```

**§5 "User and operator surfaces", first paragraph**, after "…shares, cost basis, target weights, horizon, or thesis." append: *"The watchlist reports the newest supported 10-K/10-Q/8-K indexed for an issuer; it never reports an unsupported form and never claims a filing was read."*

**`SYSTEM_DESIGN.md` line 280**, table row → `| Uncertainty is explicit | universal metric states + boring / findings-dropped / gate-withheld / pipeline-failed presentation states |`

### 2.7 W1 risks

- `outcome` defaults to `not_analyzed`, so a construction site that forgets to set it and attaches findings is now rejected by `verify_filing_entry` and withheld. That is the intended fail-closed direction, but a missed update in `canonical.py` shows up as silent withholding rather than a crash. W1-T5/T6 plus the demo-brief assertion (`view.filings` still contains DPLS/MSFT/TWKS) are the tripwire.
- Renaming `last_filing` is an API-shape change confined to four sites (grep-verified), but a stale `web/dist` bundle will still read `last_filing` — rebuild the frontend.
- `dropped_finding_count` mixes harness/compiler drops with projection-local drops. They are disjoint today (`harness.py:1057-1060` derives `published_ids` from `output.findings` and `dropped` from `_merge_drops`). A future compiler change that re-reports an already-removed finding could double-count.
- Splitting `withheld_filings` into two rendered sections changes their DTO list order (gate first, then pipeline failures) rather than strict filed-date order. Nothing asserts that order today.
- `_dropped_count` reads `view.trace.dropped_findings`, so a filing whose finalized v2 trace failed strict identity validation (`projection.py:58-66`) reports 0 drops. That is the conservative direction, and such a filing is already withheld by `llm_output_allowed`.

---

## 3. W3 — Stop hiding filings and metrics that passed

**Commit:** `feat: surface reviewed filings, reading window, and withheld metric rows`
**Depends on:** W1 (buckets on `FilingDigestEntry.outcome`).

### 3.1 Goal

Nothing that survived the deterministic publication gate is invisible.

- The brief names its reading window in human dates, reports `filings_in_window` against an unfiltered `filings_tracked_total`, and when zero filings fall inside the window but tracked filings exist it names the specific filing that sits outside and tells the user to widen the window in Settings.
- A filing that passed every check and legitimately produced zero findings is emitted as a real `FilingDigestEntry` in `BriefView.reviewed_filings` and rendered with `FilingItemCard`, so its detail page (ledger, tool count, drop codes, certificate) is reachable. The one-off `boring_filings` string is **deleted** from models, service, Markdown renderer, TS types, UI, and tests.
- `IssuerMetricsView.empty` / `MetricsView.empty` are reserved for `len(rows) == 0`; whenever rows exist all three surfaces render `MetricTable` with a deterministic summary line.
- A persisted starter metric that fails presentation-time provenance re-validation is emitted as an explicit fourth **presentation** state `withheld` (carrying only DB-trusted `formula_version` / `as_of` / `id`), and the Companies-page compressed read hardcodes the denominator to `len(STARTER_METRICS)`.

### 3.2 Why it matters

The brief window comes from the user's period preference (`_since_for_period` defaults to 90 days). A 10-K is annual: for roughly nine months a year a user narrowed to that family sees "0 in view / 0 through the gate" on the brief while the Companies page still lists the filing that passed V1/V4/V5 — the two surfaces contradict each other and the trustworthy one looks broken. A routine filing that correctly produced no finding collapses to one unlinked faint sentence, so the user cannot reach the evidence that the system did the work. An issuer whose six metrics all came back "unavailable" is byte-identical on screen to an issuer that was never synced, and the shipped copy literally ORs the two states ("XBRL facts insufficient or not yet ingested"). Worst, a stored metric that fails provenance re-validation is dropped **and removed from the denominator**, so a corrupted row renders as "✓5/5" — a stronger claim than the truth.

### 3.3 Changes

#### W3-C1 · `src/finwatch/presentation/models.py`

**Anchor:** `MetricState` (15); `IssuerMetricsView` (57-60); `BriefPeriodView` (63-66, as amended by W1-C1); `BriefView` (69-81, as amended by W1-C1); `MetricsView` (163-168).

**Change.** Add the presentation-only fourth state `withheld` to `MetricState` — the **envelope** in `metrics/envelope.py` stays three-valued and is NOT touched. Add `summary: str = ""` to `IssuerMetricsView` and `MetricsView`. Replace `BriefPeriodView.covered` with `covered_label`, add `filings_tracked_total` and `outside_window`. Replace `BriefView.boring_filings: str | None` with `reviewed_filings: list[FilingDigestEntry]`. Delete `covered` and `boring_filings` outright.

**New code** (showing the merged W1+W3 shapes):
```python
MetricState = Literal["computed", "unavailable", "not_applicable", "withheld"]


class IssuerMetricsView(BaseModel):
    ticker: str
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None
    summary: str = ""


class BriefPeriodView(BaseModel):
    covered_label: str
    filings_in_window: int
    # Attempts with a persisted analysis row, whether or not they cleared the gate.
    analyzed_filings: int
    published_filings: int = 0
    withheld_filings: int = 0
    filings_tracked_total: int = 0
    outside_window: str | None = None


class BriefView(BaseModel):
    period: BriefPeriodView
    tracked_tickers: list[str] = Field(default_factory=list)
    answer: str
    answer_posture: Posture | None = None
    filings: list[FilingDigestEntry] = Field(default_factory=list)
    gate_removed_filings: list[FilingDigestEntry] = Field(default_factory=list)
    verified_numbers: list[IssuerMetricsView] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    reviewed_filings: list[FilingDigestEntry] = Field(default_factory=list)
    withheld_filings: list[FilingDigestEntry] = Field(default_factory=list)
    tracked_but_unanalyzed: bool = False
    disclaimer: str = DISCLAIMER
    sample_data: bool = False


class MetricsView(BaseModel):
    ticker: str
    as_of: str
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None
    summary: str = ""
    before_first_filing: bool = False
```

#### W3-C2 · `src/finwatch/presentation/service.py` — imports, helpers, `_scoped_filings`

**Anchor:** module imports (1-30), `_date` (33-34), `_views` (42-52).

**Change.** Add `Filing` to the repositories import; add stdlib `Counter` and `date` **after** the plain `import` lines (ruff selects `I`, so `import x` precedes `from x import y` in the stdlib group). Drop the now-unused `FilingProjection` import. Add module-level deterministic helpers and fixed copy constants. Replace `_views` with `_scoped_filings`, returning raw `Filing` rows so `brief` can count the unfiltered scope without building a projection per filing.

**New code:**
```python
import hashlib
import json
import re
from collections import Counter
from datetime import date

from finwatch.db.repositories import LOCAL_USER_ID, Company, Computation, Filing, Repo
# ... unchanged finwatch.metrics / pipeline / preprocess / presentation imports ...
from finwatch.presentation.formatting import format_metric_value, plural_count
from finwatch.presentation.projection import in_window, load_filing_projection

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_SCOPED_FORMS = frozenset({"10-K", "10-Q", "8-K"})
_NO_METRIC_ROWS = "No SEC XBRL metric has been computed for this issuer yet."
_WITHHELD_METRIC_LABEL = "Withheld — the stored result failed provenance re-validation"
_METRIC_STATE_WORDS = {
    "computed": "computed",
    "unavailable": "unavailable",
    "not_applicable": "not applicable",
    "withheld": "withheld",
}


def _date(value: str | None) -> str:
    return (value or "")[:10]


def _human_date(value: str | None) -> str:
    """Render an ISO date as a short, locale-free, deterministic label."""
    raw = _date(value)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return raw or "unknown date"
    return f"{parsed.day} {_MONTHS[parsed.month - 1]} {parsed.year}"


def _window_label(since: str | None, until: str | None) -> str:
    """Name the reading window the brief actually applied, in human dates."""
    start = _human_date(since) if since else "inception"
    end = _human_date(until) if until else "today"
    return f"{start} → {end}"


def _metric_summary(rows: list[MetricRowView]) -> str:
    """One deterministic line keeping every rendered metric state visible and distinct."""
    counts = Counter(row.state for row in rows)
    parts = [
        f"{counts[state]} {word}"
        for state, word in _METRIC_STATE_WORDS.items()
        if counts[state]
    ]
    return f"{' · '.join(parts)} of {len(STARTER_METRICS)} starter metrics"


class PresentationService:
    def __init__(self, repo: Repo, *, user_id: str = LOCAL_USER_ID) -> None:
        self.repo = repo
        self.user_id = user_id

    def _scoped_filings(
        self, since: str | None = None, until: str | None = None
    ) -> list[Filing]:
        """Tracked, launch-supported filings, newest first, optionally window-filtered.

        Returning rows rather than projections lets ``brief`` count the unfiltered scope
        without paying for a full per-filing projection it will not render.
        """
        tracked_ciks = set(self.repo.list_tracked_ciks(self.user_id))
        filings = [
            filing
            for filing in self.repo.list_filings()
            if filing.cik in tracked_ciks
            and base_form(filing.form_type) in _SCOPED_FORMS
            and in_window(filing, since, until)
        ]
        filings.sort(key=lambda row: (row.filed_at, row.accession_number), reverse=True)
        return filings
```

> Note: `import re` above is present only if W2 has already landed (W2-C1 adds it). If W2 has not landed, omit `re`.

#### W3-C3 · `src/finwatch/presentation/service.py` — `_validated_metrics` + `_metric_rows`

> **Merged change.** This single refactor serves both W3's withheld row state and W5-C11's need for validated `MetricResult` objects in `companies()`. Do **not** implement W5-C10 separately.

**Anchor:** `def _metric_rows(self, computations: list[Computation]) -> list[MetricRowView]:` (54-113), including the two `continue` guards and `by_name[row.tool] = (row, metric)`.

**Change.** Split re-validation into `_validated_metric`, which returns `None` instead of `continue`-ing, and expose the whole mapping via `_validated_metrics`. `_metric_rows` then records every persisted starter row and renders a failed one as `state="withheld"`. Both `latest_computations` and `computations_as_of` return exactly one row per tool, so unconditional assignment cannot let a bad newest row shadow a good older row — the old `continue` had no fallback semantics to preserve. **The withheld row is built ONLY from DB-trusted columns plus a fixed label: the untrusted `result_json` payload must never reach the screen.**

**New code:**
```python
    def _validated_metric(self, row: Computation) -> MetricResult | None:
        """Return the stored envelope only while it still proves its own provenance."""
        try:
            metric = MetricResult.model_validate_json(row.result_json)
        except Exception:  # noqa: BLE001 - corrupt persisted metrics are withheld
            return None
        if (
            metric.metric != row.tool
            or metric.status.value != row.status
            or metric.formula_version != row.formula_version
            or metric.as_of != row.as_of
        ):
            return None
        if metric.status.value == "computed":
            # A computation ID is not enough provenance by itself. Every
            # rendered computed starter metric must retain typed SEC leaves.
            if not metric.inputs_used or any(
                source.value is None
                or not source.taxonomy
                or not source.tag
                or not source.unit_ref
                or not source.accession_number
                or not (source.instant or source.period_end)
                for source in metric.inputs_used
            ):
                return None
        return metric

    def _validated_metrics(
        self, computations: list[Computation]
    ) -> dict[str, tuple[Computation, MetricResult | None]]:
        """Persisted starter computations, paired with their envelope or None."""
        by_name: dict[str, tuple[Computation, MetricResult | None]] = {}
        for row in computations:
            if row.id is None or row.tool not in STARTER_METRICS:
                continue
            by_name[row.tool] = (row, self._validated_metric(row))
        return by_name

    def _metric_rows(
        self, validated: dict[str, tuple[Computation, MetricResult | None]]
    ) -> list[MetricRowView]:
        result: list[MetricRowView] = []
        for name in STARTER_METRICS:
            pair = validated.get(name)
            if pair is None:
                continue
            computation, metric = pair
            if metric is None:
                # A stored row that fails re-validation is shown as withheld, never
                # dropped: dropping it also shrank the "computed of six" denominator,
                # turning an integrity failure into a more reassuring number. Only
                # DB-trusted columns are rendered; the untrusted payload stays hidden.
                result.append(
                    MetricRowView(
                        metric=STARTER_METRIC_LABELS.get(
                            name, name.replace("_", " ").title()
                        ),
                        value="— withheld",
                        formula=computation.formula_version,
                        state="withheld",
                        state_label=_WITHHELD_METRIC_LABEL,
                        source_computation_id=computation.id,
                        effective_as_of=computation.as_of,
                    )
                )
                continue
            if metric.status.value == "computed":
                value = format_metric_value(metric)
                state_label = "Computed from SEC XBRL facts"
            elif metric.status.value == "not_applicable":
                state_label = metric.not_applicable_reason or "Not applicable for this issuer"
                value = f"— {state_label}"
            else:
                state_label = ", ".join(metric.unavailable_missing) or "Data missing"
                value = f"— {state_label}"
            result.append(
                MetricRowView(
                    metric=STARTER_METRIC_LABELS.get(
                        metric.metric, metric.metric.replace("_", " ").title()
                    ),
                    value=value,
                    formula=metric.formula_version,
                    state=metric.status.value,
                    state_label=state_label,
                    source_computation_id=computation.id,
                    effective_as_of=metric.as_of,
                )
            )
        return result
```

Both existing call sites become `self._metric_rows(self._validated_metrics(...))` — see W3-C4 and W3-C7.

#### W3-C4 · `src/finwatch/presentation/service.py` — `_issuer_metrics`

**Anchor:** `_issuer_metrics` (115-127), the `empty = (...)` expression and the return.

**Current:**
```python
        rows = self._metric_rows(computations)
        empty = (
            None
            if any(row.state == "computed" for row in rows)
            else "no verified financials yet (XBRL facts insufficient or not yet ingested)."
        )
        return IssuerMetricsView(ticker=company.ticker, rows=rows, empty=empty)
```

**New code:**
```python
        rows = self._metric_rows(self._validated_metrics(computations))
        return IssuerMetricsView(
            ticker=company.ticker,
            rows=rows,
            empty=None if rows else _NO_METRIC_ROWS,
            summary=_metric_summary(rows) if rows else "",
        )
```

#### W3-C5 · `src/finwatch/presentation/service.py` — `brief()`

**Anchor:** the `brief` body as rewritten by W1-C6.

**Change.** Rename `boring` → `reviewed` and publish the entries themselves. Carry the unfiltered scope count and, when the window is empty but tracked filings exist, name the newest out-of-window filing. Add an `outside_window` answer branch between `analyzed` and `tracked`, and stop telling the user to run analysis when the only reason the brief is empty is the reading window. **Buckets still come from W1's `outcome` field; no gate logic changes.**

**New code** (replacing the W1-C6 body):
```python
        tracked = self.repo.list_tracked_companies(self.user_id)
        scoped = self._scoped_filings(since, until)
        all_scoped = self._scoped_filings()
        views = [load_filing_projection(self.repo, filing) for filing in scoped]
        analyzed = [view for view in views if view.analysis_present]
        entries = [build_filing_entry(self.repo, view) for view in views]
        published = [entry for entry in entries if entry.outcome == "published"]
        gate_withheld = [entry for entry in entries if entry.outcome == "withheld_gate"]
        pipeline_failed = [entry for entry in entries if entry.outcome == "pipeline_failed"]
        withheld = gate_withheld + pipeline_failed
        gate_removed = [entry for entry in entries if entry.outcome == "findings_dropped"]
        reviewed = [entry for entry in entries if entry.outcome == "no_findings"]
        # Only these three buckets actually cleared the publication gate.
        cleared_gate = len(published) + len(gate_removed) + len(reviewed)

        # A filing can pass every deterministic check and still sit outside the reading
        # window — a 10-K is annual, so that is the normal state for most of the year.
        # Name the filing instead of reporting an indistinguishable empty brief.
        outside_window = None
        if not scoped and all_scoped:
            newest = all_scoped[0]
            issuer = self.repo.get_company(newest.cik)
            outside_window = (
                f"{issuer.ticker if issuer else newest.cik} "
                f"{base_form(newest.form_type)} filed {_human_date(newest.filed_at)} sits "
                "outside this reading window. Widen the reading window in Settings "
                "to include it."
            )

        tracked_tickers = sorted(company.ticker for company in tracked)
        severe = any(
            any(finding.severity in {"CRITICAL", "HIGH"} for finding in entry.findings)
            for entry in published
        )

        answer_posture = None
        if gate_withheld:
            answer = (
                f"{plural_count(len(gate_withheld), 'filing')} withheld — "
                "could not be verified."
            )
            answer_posture = "risk_review"
        elif pipeline_failed:
            answer = (
                f"{plural_count(len(pipeline_failed), 'filing')} could not be analyzed — "
                "the pipeline did not complete."
            )
            answer_posture = "insufficient_data"
        elif severe:
            answer = "A tracked company needs a critical review."
            answer_posture = "critical_review"
        elif published:
            answer = f"Important changes found in {plural_count(len(published), 'filing')}."
            answer_posture = "risk_review"
        elif gate_removed:
            answer = (
                f"Every proposed change in {plural_count(len(gate_removed), 'filing')} "
                "failed the evidence gate. Verified numbers still published."
            )
            answer_posture = "risk_review"
        elif analyzed:
            answer = (
                "Nothing important changed. "
                f"{plural_count(len(reviewed), 'routine filing')} reviewed."
            )
            answer_posture = "monitor"
        elif outside_window:
            answer = "No tracked filing falls inside your reading window."
            answer_posture = "monitor"
        elif tracked:
            answer = "No material findings yet — sync filings or run analysis."
        else:
            answer = "Add a ticker to start your brief."

        questions = [
            f"{view.ticker}: a deterministic data-quality check needs review."
            for view in analyzed
            if view.data_quality
        ]
        questions.extend(
            f"{entry.ticker}: automated verification withheld this filing."
            for entry in gate_withheld
        )
        questions.extend(
            f"{entry.ticker}: analysis did not complete, so this filing was never published."
            for entry in pipeline_failed
        )
        questions.extend(
            f"{entry.ticker}: every proposed change failed the evidence gate."
            for entry in gate_removed
        )
        return BriefView(
            period=BriefPeriodView(
                covered_label=_window_label(since, until),
                filings_in_window=len(views),
                analyzed_filings=len(analyzed),
                published_filings=cleared_gate,
                withheld_filings=len(withheld),
                filings_tracked_total=len(all_scoped),
                outside_window=outside_window,
            ),
            tracked_tickers=tracked_tickers,
            answer=answer,
            answer_posture=answer_posture,
            filings=published,
            gate_removed_filings=gate_removed,
            verified_numbers=[self._issuer_metrics(c) for c in tracked],
            open_questions=questions,
            reviewed_filings=reviewed,
            withheld_filings=withheld,
            tracked_but_unanalyzed=bool(tracked and not analyzed and not outside_window),
            sample_data=sample_data,
        )
```

#### W3-C6 · `src/finwatch/presentation/service.py` — `companies()` denominator

> **Merged into W5-C11.** Apply the denominator fix there. The rule: the ✓ denominator is `len(STARTER_METRICS)`, always, and the compressed read is built whenever `metrics.rows` is non-empty (so "✓0/6" is stated rather than blank). `STARTER_METRICS` is already imported at `service.py:9`. No existing test asserts on `compressed_verified_read`; its only consumer is `web/src/pages/CompaniesPage.tsx:27`.

#### W3-C7 · `src/finwatch/presentation/service.py` — `metrics()`

**Anchor:** `metrics(self, ticker, *, as_of)` (461-483), the `empty = (...)` expression and the return.

**New code:**
```python
        rows = self._metric_rows(
            self._validated_metrics(self.repo.computations_as_of(ticker.upper(), as_of))
        )
        empty = (
            None
            if rows
            else (
                "No SEC XBRL metric existed at this as-of date."
                if before_first
                else _NO_METRIC_ROWS
            )
        )
        return MetricsView(
            ticker=ticker.upper(),
            as_of=as_of,
            rows=rows,
            empty=empty,
            summary=_metric_summary(rows) if rows else "",
            before_first_filing=before_first,
        )
```

#### W3-C8 · `src/finwatch/digest/render.py`

**Anchor:** `_header` (53-67, as amended by W1-C8); the `out.extend([...])` block inside `_verified_numbers_section` (135-141); `_boring_section` (165-168); the `lines.extend(_boring_section(...))` call (179).

**Change.** Mirror the brief-level field changes. `_boring_section` is **deleted** and replaced by `_reviewed_section`. Each issuer table gains the summary line. `FilingDigestEntry` is already imported (line 19). **Leave the ✓/— marker column and its legend unchanged** — the value column already reads "— withheld" and the summary line names every state, so no marker-vocabulary change is needed and the existing `"| ✓ |"` assertion keeps holding.

**New code:**
```python
def _header(brief: BriefView) -> list[str]:
    tracked = ", ".join(brief.tracked_tickers) or "none"
    lines = [
        "# finwatch digest",
        "",
        f"> {_markdown_text(brief.answer)}",
        "",
        f"- **Reading window:** {_markdown_text(brief.period.covered_label)}",
        f"- **Holdings tracked:** {_markdown_text(tracked)}",
        (
            f"- **Filings in window:** {brief.period.filings_in_window} of "
            f"{brief.period.filings_tracked_total} tracked "
            f"· **Analysis on file:** {brief.period.analyzed_filings} "
            f"· **Published:** {brief.period.published_filings} "
            f"· **Withheld:** {brief.period.withheld_filings}"
        ),
    ]
    if brief.period.outside_window:
        lines.append(
            f"- **Outside the window:** {_markdown_text(brief.period.outside_window)}"
        )
    lines.append("")
    return lines


def _reviewed_section(entries: list[FilingDigestEntry]) -> list[str]:
    """Routine filings are a real result: list them as linkable, identified entries."""
    if not entries:
        return []
    out = ["## Reviewed — nothing material", ""]
    for entry in entries:
        out.append(
            f"- [{_markdown_text(entry.ticker)} — {_markdown_text(entry.form)} filed "
            f"{_markdown_text(entry.filed)}]({entry.edgar_url}) — reviewed; "
            "no finding cleared the evidence gate."
        )
    out.append("")
    return out
```

Inside `_verified_numbers_section`, replacing the existing `out.extend([...])`:
```python
        out.extend(
            [
                f"### {_markdown_text(issuer.ticker)}",
                "",
                f"_{_markdown_text(issuer.summary)}_",
                "",
                "| Metric | Value | Computed as of | Formula | ✓ |",
                "|---|---|---|---|---|",
            ]
        )
```

Inside `render_brief_markdown`, replacing the `_boring_section` call:
```python
    lines.extend(_reviewed_section(brief.reviewed_filings))
```

#### W3-C9 · `web/src/types.ts`

**New code:**
```typescript
export type MetricState = "computed" | "unavailable" | "not_applicable" | "withheld";
export interface IssuerMetrics { ticker: string; rows: MetricRow[]; empty: string | null; summary: string }
export interface Brief { period: { covered_label: string; filings_in_window: number; analyzed_filings: number; published_filings: number; withheld_filings: number; filings_tracked_total: number; outside_window: string | null }; tracked_tickers: string[]; answer: string; answer_posture: Posture | null; filings: FilingDigestEntry[]; gate_removed_filings: FilingDigestEntry[]; verified_numbers: IssuerMetrics[]; open_questions: string[]; reviewed_filings: FilingDigestEntry[]; withheld_filings: FilingDigestEntry[]; tracked_but_unanalyzed: boolean; disclaimer: string; sample_data: boolean }
export interface Metrics { ticker: string; as_of: string; rows: MetricRow[]; empty: string | null; summary: string; before_first_filing: boolean }
```

`FilingDetail.verified_numbers` reuses `IssuerMetrics`, so it picks up `summary` automatically; the only existing TS fixture that builds one is `FilingPage.test.tsx`, which sets `verified_numbers: null`.

#### W3-C10 · `web/src/components/MetricTable.tsx`

> **Merged into W5-C4.** W3's contribution to that file is: accept an optional `summary` prop rendered above the table, and render four visibly distinct states instead of collapsing three into `"—"`. Ship W3-C10 as written below, then W5-C4 removes `showComputedMark` and W6-C7 removes the `.trust.missing` class.

**New code (W3 stage):**
```tsx
import type { MetricRow, MetricState } from "../types";

const STATE_TEXT: Record<MetricState, string> = {
  computed: "Computed",
  unavailable: "Unavailable",
  not_applicable: "Not applicable",
  withheld: "Withheld",
};

const STATE_CLASS: Record<MetricState, string> = {
  computed: "computed",
  unavailable: "missing",
  not_applicable: "missing",
  withheld: "withheld",
};

export function MetricTable({ rows, summary, showComputedMark = true }: { rows: MetricRow[]; summary?: string; showComputedMark?: boolean }) {
  return <div className="metric-block">
    {summary && <p className="metric-summary">{summary}</p>}
    <div className="table-scroll"><table className="metric-table">
      <caption className="sr-only">Deterministic SEC XBRL metric results</caption>
      <thead><tr><th>Metric</th><th>Value</th><th>Method &amp; source</th><th>Status</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.source_computation_id}>
        <td>{row.metric}</td><td>{row.value}</td><td><code className="formula">{row.formula}</code><span className="metric-source">computation #{row.source_computation_id} · computed as of {row.effective_as_of}</span></td>
        <td><span className={`trust ${STATE_CLASS[row.state]}`} title={row.state_label} aria-label={row.state_label}>{row.state === "computed" && showComputedMark ? "✓ " : ""}{STATE_TEXT[row.state]}</span></td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}
```

#### W3-C11 · `web/src/pages/BriefPage.tsx`

**Anchor:** `<div className="brief-stats">` (39-44, as amended by W1-C11); the numbered section lines (51-53); the faint boring line (54).

**Change.** Label the stat as a reading window with the backend's human label and a Settings link; show `in_window of tracked_total`; render the out-of-window sentence as a notice; add the linkable "Reviewed — nothing material" section using `FilingItemCard`; pass `summary` into `MetricTable`; delete the faint boring line. **Renumber the section kickers 01/02/03/04.** `Link` is already imported (line 2).

**New code:**
```tsx
      <div className="brief-stats">
        <div><span>Reading window</span><strong className="window-label">{brief.period.covered_label}</strong><small><Link className="text-link" to="/settings">Change in Settings</Link></small></div>
        <div><span>Filings</span><strong>{brief.period.filings_in_window} of {brief.period.filings_tracked_total}</strong><small>in the window</small></div>
        <div><span>Published</span><strong>{brief.period.published_filings}</strong><small>{brief.period.withheld_filings > 0 ? `${brief.period.withheld_filings} held back` : "cleared the gate"}</small></div>
        <div><span>Tracking</span><strong>{trackedTickers.length}</strong><small>{trackedTickers.length === 1 ? "company" : "companies"}</small></div>
      </div>
    </section>
    {brief.period.outside_window && <div className="notice neutral window-note">{brief.period.outside_window} <Link className="text-link" to="/settings">Open settings</Link></div>}

{/* ...unchanged ticker strip / actionError / JobProgress / empty-invitation / guidance-note / withheld / not-analyzed / gate-removed sections... */}

    {trackedTickers.length > 0 && <section className="section"><SectionHeader index="01 · Filing changes" title="What changed" /><p className="metric-caption">The model selects significance; RipplX independently checks that every displayed quotation matches the SEC filing exactly.</p>{brief.filings.length ? brief.filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} />) : <div className="empty-state"><span aria-hidden="true">—</span><div><strong>No evidence-backed changes selected</strong><p>{brief.tracked_but_unanalyzed ? "No filing has completed analysis yet." : "The analyzed filings were routine or did not produce a finding that cleared the gate."}</p></div></div>}</section>}
    {brief.reviewed_filings.length > 0 && <section className="section"><SectionHeader index="02 · Reviewed" title="Reviewed — nothing material" /><p className="metric-caption">These filings cleared every deterministic check and legitimately produced no finding. Open one to read its ledger, tool trace, and verification certificate.</p>{brief.reviewed_filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} />)}</section>}
    {brief.verified_numbers.length > 0 && <section className="section"><SectionHeader index="03 · SEC XBRL" title="Verified numbers" /><p className="metric-caption">Versioned formulas compute these values directly from SEC XBRL facts—never from the language model.</p>{brief.verified_numbers.map(issuer => <article className="issuer-block" key={issuer.ticker}><h3 className="issuer-title">{issuer.ticker}</h3>{issuer.empty ? <p className="empty-line">{issuer.ticker}: {issuer.empty}</p> : <MetricTable rows={issuer.rows} summary={issuer.summary} />}</article>)}</section>}
    {trackedTickers.length > 0 && <section className="section"><SectionHeader index="04 · Follow-up" title="Open questions" />{brief.open_questions.length ? <ul className="question-list">{brief.open_questions.map((question, index) => <li className="muted" key={index}>{question}</li>)}</ul> : <div className="empty-state compact"><span aria-hidden="true">—</span><div><strong>No open questions</strong><p>Nothing in this brief needs a follow-up review.</p></div></div>}</section>}
    <DisclaimerFooter text={brief.disclaimer} />
```

#### W3-C12 · `web/src/pages/CompanyPage.tsx`

**Anchor:** line 28, the trailing fragment of the `reading-section`.

**New code:**
```tsx
{metrics.empty ? <div className="notice neutral">{metrics.before_first_filing ? "No filings or computed numbers existed at this date." : `${metrics.ticker}: ${metrics.empty}`}</div> : <MetricTable rows={metrics.rows} summary={metrics.summary} />}
```

#### W3-C13 · `web/src/pages/FilingPage.tsx`

**Anchor:** line 87, inside the `numbers-heading` section.

**New code:**
```tsx
      {detail.verified_numbers && detail.verified_numbers.rows.length > 0 ? <MetricTable rows={detail.verified_numbers.rows} summary={detail.verified_numbers.summary} showComputedMark={!withheld} /> : <p className="empty-line">{detail.verified_numbers?.empty ?? "No SEC XBRL metric has been computed for this issuer yet."}</p>}
```

#### W3-C14 · `web/src/styles/global.css`

**Anchor:** `.trust.computed { color: var(--color-verified); }` (711-713), immediately before `.issuer-block`; and `.brief-stats strong { ... font-size: 22px; }` (437-443).

**Change.** `--color-withheld` already exists at `tokens.css:25` and is currently unused by `MetricTable`.

**New code:**
```css
.trust.computed {
  color: var(--color-verified);
}

.trust.withheld {
  color: var(--color-withheld);
}

.metric-summary {
  margin-bottom: 8px;
  color: var(--color-muted);
  font-family: var(--font-mono);
  font-size: var(--text-caption);
}

.brief-stats strong.window-label {
  font-family: var(--font-mono);
  font-size: 13px;
  letter-spacing: -0.01em;
}

.window-note {
  margin-top: 14px;
}
```

### 3.4 W3 tests

| ID | File | Test | Asserts | Fails before |
|---|---|---|---|---|
| W3-T1 | `tests/test_presentation.py` | `test_brief_names_the_filing_that_sits_outside_the_reading_window` | `build_demo_db()`; `brief(since="2099-01-01")`. Asserts `filings_in_window == 0`, `filings_tracked_total == 5`, `covered_label == "1 Jan 2099 → today"`, `"AAPL 8-K filed 30 Apr 2026" in outside_window`, `"Settings" in outside_window`, `answer == "No tracked filing falls inside your reading window."`, `tracked_but_unanalyzed is False`. | ✅ |
| W3-T2 | `tests/test_presentation.py` | `test_routine_filings_publish_as_linkable_reviewed_entries` | Demo DB, `brief(since=DEMO_SINCE)`. Asserts `[(e.ticker, e.form) for e in view.reviewed_filings] == [("AAPL", "8-K"), ("AAPL", "10-Q")]`, every entry has `findings == []`, `withheld is False`, non-empty `accession`, `edgar_url` starting `https://www.sec.gov/`. Also `not hasattr(view, "boring_filings")`. | ✅ |
| **W3-T3** | `tests/test_presentation.py` | `test_metric_failing_provenance_revalidation_is_withheld_not_hidden` | **ADVERSARIAL.** Rewrite of `test_computed_metric_without_typed_sec_inputs_is_not_rendered` (line 159), same fixture: tampered MSFT `liquidity_basics` with `inputs_used=[]`, `components={"cash": 999_000_000.0, "net_debt": 0.0}`, `formula_version="liquidity_basics.v2"`, `as_of="2024-04-25"`, `created_at="later"`. Asserts `row.state == "withheld"`, `state_label.startswith("Withheld")`, `value == "— withheld"`, `effective_as_of == "2024-04-25"` (DB column), `"999" not in row.value`, **`"999" not in view.model_dump_json()`**, `"withheld" in view.summary`, `view.empty is None`. | ✅ |
| W3-T4 | `tests/test_presentation.py` | `test_all_unavailable_metrics_still_render_a_summarized_table` | `Repo(init_db(":memory:"))`; upsert+track `ZZZ` and `WWW`. One `revenue_growth` Computation for ZZZ, `status="unavailable"`, `unavailable_missing=["us-gaap:Revenues"]`. Asserts ZZZ: `empty is None`, `len(rows) == 1`, `state == "unavailable"`, `"us-gaap:Revenues" in state_label`, `summary == "1 unavailable of 6 starter metrics"`. WWW: `rows == []`, `empty == "No SEC XBRL metric has been computed for this issuer yet."`, `"insufficient" not in empty`. Add `Company`, `init_db` to the module's `from finwatch.db import ...`. | ✅ |
| W3-T5 | `tests/test_presentation.py` | `test_compressed_read_denominator_is_the_fixed_starter_catalog` | Demo DB + the same tampered MSFT computation; `companies()`. Asserts `compressed_verified_read is not None`, `.endswith(f"/{len(STARTER_METRICS)}")`, `"/5" not in ...`. Import `STARTER_METRICS`. | ✅ |
| W3-T6 | `web/src/components/components.test.tsx` | `distinguishes computed, unavailable, not-applicable, and withheld metrics` | Rename/extend the case at line 25. Add a fourth row `{ metric: "Liquidity", value: "— withheld", formula: "liquidity_basics.v2", state: "withheld", state_label: "Withheld — the stored result failed provenance re-validation", source_computation_id: 44, effective_as_of: "2025-06-30" }`, pass `summary="3 computed · 1 withheld of 6 starter metrics"`. Replace lines 31-33 with four `getByLabelText(...).toHaveTextContent(...)` assertions and `getByText(/of 6 starter metrics/)`. Keep `computation #41`. | ✅ |
| W3-T7 | `web/src/pages/BriefPage.test.tsx` | `shows the reading window, names out-of-window filings, and links reviewed filings` | New file. Stub fetch with a full `Brief` (`covered_label: "21 Apr 2026 → today"`, `filings_in_window: 0`, `filings_tracked_total: 1`, `outside_window` sentence, one `reviewed_filings` entry `a-1`). Render inside `MemoryRouter` + `BootstrapContext.Provider`. Asserts "Reading window", "21 Apr 2026 → today", "0 of 1", `/sits outside this reading window/`, `getByRole("link", { name: "Open settings" })` href `/settings`, "Reviewed — nothing material", `getByRole("link", { name: /AAPL/ })` href `/filings/a-1`, and `queryByText("Filings are ready for analysis.")` null. | ✅ |

**Existing expectations that MUST be updated in the same commit:**

- `tests/test_presentation.py` `test_demo_projection_preserves_digest_order_and_trust_data` (line 8): replace the `boring_filings` assertion (22-24) with `[(e.ticker, e.form) for e in view.reviewed_filings] == [("AAPL", "8-K"), ("AAPL", "10-Q")]`; add `assert view.period.filings_tracked_total == 5`.
- `tests/test_presentation.py` `test_untracking_retains_company_and_filings` (line 44): replace line 58 with `assert all(entry.ticker != "DPLS" for entry in brief.reviewed_filings)`; add `assert brief.period.filings_tracked_total == 4`.
- `tests/test_digest.py:109` `test_markdown_is_a_pure_serialization_of_the_canonical_brief`: build `BriefPeriodView(covered_label="1 Jan 2024 → 30 Jun 2024", filings_in_window=2, analyzed_filings=2, filings_tracked_total=3, outside_window=None)`; give the `IssuerMetricsView` `summary="1 computed of 6 starter metrics"`; replace `boring_filings=...` with `reviewed_filings=[FilingDigestEntry(accession="a-2", ticker="YYY", form="10-Q", filed="2024-05-02", edgar_url=url)]`. In the expected-substrings tuple replace `brief.boring_filings` with `"## Reviewed — nothing material"` and add `"Reading window"`, `"2 of 3 tracked"`, `"1 computed of 6 starter metrics"`. Keep the `claim-1`, `<script>`, `&lt;script&gt;`, and `"| Metric | Value | Computed as of | Formula | ✓ |"` assertions unchanged.
- `tests/test_digest.py:319` `test_routine_filing_lands_in_boring_not_dropped` → rename to `test_routine_filing_lands_in_reviewed_not_dropped`; final assertion becomes `"## Reviewed — nothing material" in md`, `"QQQ — 8-K filed 2024-06-10" in md`, `"https://www.sec.gov/Archives/" in md`.
- `tests/test_digest.py:45` `test_demo_runs_fast_and_covers_every_section`: in the required-header tuple (52-58) replace `"## Boring filings"` with `"## Reviewed — nothing material"`.
- `tests/test_digest.py:81` `test_demo_boring_and_disclaimer` → rename `test_demo_reviewed_and_disclaimer`; replace the routine-filings assertion with `"## Reviewed — nothing material" in md` and `"AAPL — 8-K filed 2026-04-30" in md`; keep the disclaimer assertion.
- `tests/test_digest.py:72` `test_demo_verified_numbers_table_is_formula_stamped_and_checked`: line 78 becomes `"DPLS:** No SEC XBRL metric has been computed for this issuer yet." in md`; add `"of 6 starter metrics" in md`.
- `tests/test_digest.py:192` `test_render_digest_serializes_the_same_brief_used_by_the_browser_api` must pass **unchanged** — it guards the §11 pure-serialization requirement.

### 3.5 W3 acceptance criteria

1. `uv run ruff check .`, `uv run pytest -q`, `cd web && npm test -- --run && npm run typecheck && npm run build` all green; no test deleted to make a change pass.
2. With every tracked filing outside the reading window, `/api/brief` returns `filings_in_window: 0`, `filings_tracked_total > 0`, and an `outside_window` sentence naming ticker, form, human filed date, and Settings; the UI renders it and does not show "Filings are ready for analysis."
3. A filing with `status='verified'` and zero findings appears in `reviewed_filings`, renders as a `FilingItemCard`, and its `/filings/:accession` page is reachable and still exposes `certificate_url`.
4. An issuer whose six metrics are all `unavailable` renders a full `MetricTable` with summary "6 unavailable of 6 starter metrics"; only an issuer with zero persisted starter rows produces `empty`; no user-facing string ORs "insufficient or not yet ingested".
5. A starter computation whose stored `result_json` fails re-validation renders as a `withheld` row built solely from DB columns; **the tampered payload appears nowhere in the serialized DTO.**
6. `compressed_verified_read` always ends in `/6`. `MetricTable` renders four visibly distinct labels.
7. `grep -rn "boring_filings\|period\.covered\b\|_boring_section\|Boring filings" src tests web/src` returns nothing.
8. `digest/render.py` still serializes only the passed `BriefView`.
9. **No verifier, compiler, canonical-projection, or metrics-envelope file is modified.** `git diff --name-only` touches none of `verify/*.py`, `presentation/canonical.py`, `metrics/envelope.py`, `metrics/formulas.py`, `llm/*.py`, `core/types.py`. `tests/test_verifier_mutations.py`, `tests/test_presentation_fail_closed.py`, `tests/test_launch_projection.py` pass unchanged.
10. `cmp AGENTS.md CLAUDE.md` exits 0.

### 3.6 W3 doc updates (both mirrors, same commit)

- **§8**, appended to the paragraph beginning "Presentation language stays narrower than the accounting facts": *"The metric envelope itself remains three-valued. Presentation adds one further row state, `withheld`, for a persisted starter computation that fails presentation-time provenance re-validation; such a row is rendered from trusted database columns only, never from the payload that failed, and it still counts against the fixed six-metric denominator. Metric rows are never silently dropped, and a metric table is rendered whenever any starter row exists — an all-unavailable issuer must be distinguishable from an issuer that was never synced."*
- **§11**, replace `- boring filings are a valid compact result;` with `- routine filings are a valid result and publish as identified, linkable “Reviewed — nothing material” entries, not as an unlinked summary sentence;`
- **§11**, insert after `- exact quotations link to HTTPS SEC pages;`: `- the brief states the reading window it applied in human dates, reports in-window filings against the unfiltered tracked total, and names any tracked filing that passed the gate but falls outside the window;`
- **`SYSTEM_DESIGN.md` line 280** right cell → `universal metric states (plus a presentation-only `withheld` row state) + reviewed/withheld filing presentation states`

### 3.7 W3 risks

- Adding `withheld` to `MetricState` risks being read as a fourth *envelope* state. It is not. Keep the presentation-only scope explicit in the doc wording and the code comment.
- The withheld row renders DB columns only. If a future edit reaches into `row.result_json` for a nicer label, an unvalidated value reaches the screen — exactly the leak the old drop prevented. **The `"999" not in view.model_dump_json()` assertion is the guard; do not weaken it.**
- `brief()` now calls `_scoped_filings()` twice — two cheap `list_filings()` passes, no extra projection loads. Do not "optimize" by building projections for the unfiltered set.
- `tracked_but_unanalyzed` now depends on `outside_window`. If a user has both an out-of-window analyzed filing and genuinely unanalyzed in-window filings, `outside_window` is `None` and the guidance note behaves as before — verify in review.
- Renumbering the BriefPage kickers touches lines W4 and W5 also edit; land W3 before them (as ordered).

---

## 4. W2 — Render the V1/V4/V5 verification gate on the filing page

**Commit:** `feat: render the deterministic publication gate on the filing page`
**Blocked by Decision D1.**

### 4.1 Goal

`/filings/{accession}` renders the deterministic publication gate the backend already computes and already serializes. Directly under the outcome banner, a "What was checked" band shows the overall verdict and one row per persisted verification result: the machine check id (V1, V4, V5, V2a…V2d), a fixed human-readable label, and the verdict. Rows are grouped so the blocking gate (V1/V4/V5) is visually and textually separated from V2 accounting data quality, labelled "Non-blocking — reported, never a gate". `VerificationCheckView.detail` stops being permanently null — but **only** for the deterministic V2 family, sanitized and truncated. `data_quality_report` stamps `severity="info"` on non-failing V2 results.

### 4.2 Why it matters

`PresentationService.filing()` builds a complete `VerificationView` (service.py:212-234), ships it on the DTO (models.py:144), and the frontend types it (types.ts:12, :37) — and no JSX in `web/src` ever reads `detail.verification`. The only place a check id appears is inside the certificate panel, behind a collapsed toggle that additionally requires `certificate_url` to be non-null and is hidden entirely in demo mode (`FilingPage.tsx:91` renders `ProvenancePanel` only when `!demo`). So the page asserts "Exact evidence checked" (`FilingPage.tsx:80`) as an unbacked badge while the actual evidence is dead weight on the wire. Rendering it converts a claim into a receipt.

**Verified against the real demo DB:** every analyzed filing persists exactly `['V1','V4','V5','V2a','V2b','V2c','V2d']` in that order. MSFT `0000950170-24-048288` holds `V2c pass blocking detail='rev=168088000000.0 gp=115856000000.0 oi=69916000000.0'`. **V3 does not exist in persisted data at all** — `grep -rn '"V3"' src/` returns zero hits; do not render or synthesize a V3 row, and leave the §10 sentence "V3: not applicable" unchanged.

### 4.3 Changes

#### W2-C1 · `src/finwatch/presentation/service.py`

**Anchor:** the stdlib import block; new constants + helper immediately after `_date`; the `VerificationCheckView(...)` construction inside `PresentationService.filing` (226-233).

**Change.** Populate `detail` for the V2 family only. V2 details are deterministic strings built from SEC XBRL values inside `verify/checks.py` plus fixed English skip reasons from `verify/orchestrator.py` — no model-authored bytes. V1/V4/V5 details quote the model-authored rendered line (`orphan number '{tok.raw}' at pos {tok.position}`, checks.py:265-267) or a claim id (checks.py:356-374), so they stay server-side.

**The allow-list is keyed on CHECK ID, not charset** — a charset filter would not stop the fail-closed sentinel pinned by `tests/test_presentation_fail_closed.py` (`UNVERIFIED_CHECK_DETAIL_7359`, plain alphanumerics). The charset pass is defence in depth against a corrupted persisted row.

**New code:**
```python
# --- stdlib import block: add `re` -----------------------------------------
import hashlib
import json
import re


# --- new module-level constants + helper, directly after `_date` -----------

# Only the deterministic V2 accounting-identity family has a user-facing detail
# string: those details are built from SEC XBRL values and fixed English skip
# reasons. V1/V4/V5 details quote the model-authored rendered line or a claim id,
# so they stay server-side (AGENTS.md §12) and the browser renders a fixed label
# per check id instead.
_DATA_QUALITY_CHECK = re.compile(r"V2[a-z]?")
# Defence in depth against a corrupted persisted row: replace anything outside the
# printable set the V2 producers emit (control characters, angle brackets, quotes).
_DETAIL_DISALLOWED = re.compile(r"[^0-9A-Za-z Δ_.,;:/()+\-=%]")
_DETAIL_MAX_CHARS = 200


def _check_detail(check_id: str, detail: str | None) -> str | None:
    """Sanitized deterministic detail for a V2 check; None for every gated check."""
    if not detail or not _DATA_QUALITY_CHECK.fullmatch(check_id):
        return None
    cleaned = " ".join(_DETAIL_DISALLOWED.sub(" ", detail).split())
    return cleaned[:_DETAIL_MAX_CHARS] or None


# --- inside PresentationService.filing(): pass the sanitized detail ---------
                    checks=[
                        VerificationCheckView(
                            check_id=c.check_id,
                            verdict=c.verdict.upper(),
                            severity=c.severity,
                            detail=_check_detail(c.check_id, c.detail),
                        )
                        for c in checks
                    ],
```

#### W2-C2 · `src/finwatch/verify/orchestrator.py`

**Anchor:** `data_quality_report`, the final `else` branch of the reclassification loop (64-65).

**Current:**
```python
        elif r.verdict == "fail":
            # data-quality signal, not a blocking gate (see docstring)
            out.append(CheckResult(check_id=r.check_id, verdict="warn", severity="warning",
                                   detail=r.detail))
        else:
            out.append(r)
    return out
```

**Change.** `check_v2_identities` constructs pass/fail results with `severity="blocking"` (checks.py:285-288, 300-303, 331-335); `data_quality_report` downgrades only failures, so a **passing** V2 identity is persisted as `blocking` and rendered verbatim by `ProvenancePanel.tsx:113` — a visible contradiction of §10. Nothing gates on this value (`projection.py:79-81` requires `verdict == "fail"` too; `projection.py:74-76` reads only the verdict; `pipeline/orchestrator.py:397-399` requires `verdict == "fail"`), so this is display/semantics only.

**New code:**
```python
        elif r.verdict == "fail":
            # data-quality signal, not a blocking gate (see docstring)
            out.append(CheckResult(check_id=r.check_id, verdict="warn", severity="warning",
                                   detail=r.detail))
        else:
            # V2 is never a gate. check_v2_identities builds pass/fail rows with
            # severity="blocking"; keeping that on a passing row would let the
            # certificate surface present an accounting identity as a publication
            # gate. Non-failing V2 results are informational, full stop.
            out.append(CheckResult(check_id=r.check_id, verdict=r.verdict,
                                   severity="info", detail=r.detail))
    return out
```

> After this change, the demo database's `V2c` row is persisted with `severity='info'`. `build_demo_db()` runs the real `data_quality_report`, so this is expected and **no fixture needs regenerating**. No test asserts that value.

#### W2-C3 · `web/src/types.ts`

**Change.** Extract the duplicated inline check shape into one named interface. Pure refactor, no shape change.

**New code:**
```typescript
export interface VerificationCheck { check_id: string; verdict: string; severity: string; detail: string | null }
export interface Verification { verdict: "PASS" | "PASS_WITH_WARNINGS" | "FAIL"; checks: VerificationCheck[] }

// ... and inside `export interface Certificate`, replace the inline literal with:
  verification: VerificationCheck[];
```

#### W2-C4 · `web/src/components/VerificationBand.tsx` (new file)

**Change.** Rows are partitioned into three groups by check id: the blocking gate is the documented required set `{V1, V4, V5}`, mirroring `_REQUIRED_PUBLICATION_CHECKS` at `projection.py:11`; data quality is `/^V2[a-z]?$/`; anything else falls into a neutral "Other recorded checks" group that makes **no** blocking/non-blocking claim. `severity` is deliberately **not** rendered — the group heading carries the blocking statement, which is legible and immune to stale persisted severities. Duplicate check ids are legitimate (V1 emits one row per orphan number, V4 one per bad citation), so React keys are `${check_id}-${index}`. Returns null when there is no verification or no checks, so existing FilingPage tests (fixture passes `checks: []`) are unaffected.

**New code:**
```tsx
import type { Verification, VerificationCheck } from "../types";

/** Human name for each persisted deterministic check. The machine identity stays
 *  visible beside it; an unknown id falls back to the raw id rather than guessing. */
export const CHECK_LABEL: Record<string, string> = {
  V1: "Every number shown traces to SEC XBRL or an exact quotation",
  V2a: "Balance sheet ties out: assets = liabilities + equity",
  V2b: "Balance-sheet cash change ties to the cash-flow statement",
  V2c: "Revenue is at least gross profit, which is at least operating income",
  V2d: "Segment dimensions",
  V4: "Every quotation is verbatim at its declared position in the filing",
  V5: "Output schema, disclaimer, and no-advice hygiene",
};

const VERDICT_LABEL: Record<string, string> = {
  PASS: "Passed",
  FAIL: "Failed",
  WARN: "Warning",
  SKIPPED_NOT_APPLICABLE: "Not applicable",
};

const OVERALL: Record<Verification["verdict"], { label: string; tone: string }> = {
  PASS: { label: "All checks passed", tone: "verified" },
  PASS_WITH_WARNINGS: { label: "Passed with data-quality warnings", tone: "amber" },
  FAIL: { label: "A blocking check failed", tone: "critical" },
};

// The required publication gate, mirroring _REQUIRED_PUBLICATION_CHECKS in
// presentation/projection.py. Anything outside these two families is grouped
// without a blocking claim rather than assumed non-blocking.
const GATE_CHECKS = new Set(["V1", "V4", "V5"]);
const DATA_QUALITY_CHECK = /^V2[a-z]?$/;

export function checkLabel(checkId: string): string {
  return CHECK_LABEL[checkId] ?? checkId;
}

export function verdictLabel(verdict: string): string {
  return VERDICT_LABEL[verdict] ?? verdict.replaceAll("_", " ").toLowerCase();
}

function CheckGroup({ title, note, checks }: { title: string; note: string; checks: VerificationCheck[] }) {
  if (checks.length === 0) return null;
  return <div className="check-group">
    <h3 className="check-group-title">{title}<small>{note}</small></h3>
    {/* V1 and V4 emit one row per violation, so a check id is not unique. */}
    <div className="check-list">{checks.map((check, index) => <div className={`gate-check-row ${check.verdict.toLowerCase()}`} key={`${check.check_id}-${index}`}>
      <code>{check.check_id}</code>
      <span className="check-name">{checkLabel(check.check_id)}</span>
      <span className="check-verdict">{verdictLabel(check.verdict)}</span>
      {check.detail && <small className="check-detail">{check.detail}</small>}
    </div>)}</div>
  </div>;
}

export function VerificationBand({ verification }: { verification: Verification | null }) {
  if (!verification || verification.checks.length === 0) return null;
  const gate = verification.checks.filter(check => GATE_CHECKS.has(check.check_id));
  const dataQuality = verification.checks.filter(check => DATA_QUALITY_CHECK.test(check.check_id));
  const other = verification.checks.filter(
    check => !GATE_CHECKS.has(check.check_id) && !DATA_QUALITY_CHECK.test(check.check_id),
  );
  const overall = OVERALL[verification.verdict];

  return <section className="section verification-band" aria-labelledby="verification-heading">
    <header className="reading-heading">
      <div><p className="section-kicker">Deterministic publication gate</p><h2 id="verification-heading">What was checked</h2></div>
      <span className={`pill ${overall.tone}`}>{overall.label}</span>
    </header>
    <p className="metric-caption">These checks prove provenance, exactness, and hygiene. They do not decide whether a change matters to you.</p>
    <CheckGroup title="Publication gate" note="Blocking — a failure withholds the finding" checks={gate} />
    <CheckGroup title="Accounting data quality" note="Non-blocking — reported, never a gate" checks={dataQuality} />
    <CheckGroup title="Other recorded checks" note="Recorded for this attempt" checks={other} />
  </section>;
}
```

#### W2-C5 · `web/src/pages/FilingPage.tsx`

**Change.** Import `VerificationBand` and mount it directly under the outcome banner, above "What changed". It renders unconditionally, **including in `?demo=1`** (unlike `ProvenancePanel`): the band contains only deterministic verification results, never model output. It also renders when the filing is withheld, because a FAIL verdict naming the failed check is exactly the explanation the withheld banner owes the user.

**New code:**
```tsx
import { ProvenancePanel } from "../components/ProvenancePanel";
import { VerificationBand } from "../components/VerificationBand";
import { useResource } from "../hooks/useResource";

// ... inside the returned JSX, immediately after the outcome-banner lines
// and immediately before the `changes-heading` section:

    <VerificationBand verification={detail.verification} />
```

#### W2-C6 · `web/src/components/ProvenancePanel.tsx`

**Anchor:** the certificate "Verification checks" list (line 113).

**Change.** Latent duplicate-key bug on the same data: V1 emits one `CheckResult` per orphan number and V4 one per bad citation, so `certificate.verification` can legitimately contain several rows sharing a check id, and React logs a duplicate-key error and can mis-reconcile them.

**New code:**
```tsx
{certificate.verification.length > 0 && <section className="proof-block"><h3>Verification checks</h3><div className="check-list">{certificate.verification.map((check, index) => <div className={`check-row ${check.verdict.toLowerCase()}`} key={`${check.check_id}-${index}`}><code>{check.check_id}</code><span>{check.verdict}</span><small>{check.severity}</small></div>)}</div></section>}
```

#### W2-C7 · `web/src/styles/global.css`

**Anchor:** immediately after `.check-row.fail span { ... }` (1052-1055) and before `.certificate-download { margin-top: 34px; }` (1057).

**Change.** Introduces `.gate-check-row` rather than reusing `.check-row` for two concrete reasons: `.check-row`'s three-column grid (1026) has no slot for the wrapped detail line, and `.check-row.pass span` (1041) colours **every** span green, which would turn the human check name into a verdict-coloured string. Keeping `.check-row` untouched leaves the certificate panel visually unchanged. Exactly one new `.pill` modifier is added — `.pill.verified` — because `.pill.amber` (598) and `.pill.critical` (603) already exist and are reused.

**New code:**
```css
.verification-band {
  margin-top: 34px;
}

.check-group + .check-group {
  margin-top: 26px;
}

.check-group-title {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: baseline;
  margin-bottom: 6px;
  font-family: var(--font-sans);
  font-size: 15px;
  font-weight: 750;
  letter-spacing: 0;
}

.check-group-title small {
  color: var(--color-muted);
  font-size: var(--text-caption);
  font-weight: 400;
}

.gate-check-row {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr) minmax(110px, auto);
  gap: 15px;
  align-items: baseline;
  padding: 11px 12px;
  border-bottom: var(--border-hair);
}

.gate-check-row > code {
  color: var(--color-muted);
  font-size: var(--text-caption);
}

.gate-check-row .check-name {
  color: var(--color-body);
  font-size: var(--text-body-sm);
}

.gate-check-row .check-verdict {
  color: var(--color-muted);
  font-size: var(--text-caption);
  font-weight: 700;
  text-align: right;
}

.gate-check-row.pass .check-verdict {
  color: var(--color-verified);
}

.gate-check-row.fail .check-verdict {
  color: var(--color-critical);
}

.gate-check-row.warn .check-verdict {
  color: var(--color-warn);
}

.gate-check-row .check-detail {
  grid-column: 2 / -1;
  margin-top: 4px;
  color: var(--color-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  overflow-wrap: anywhere;
}

.pill.verified {
  color: var(--color-verified);
  border-color: var(--color-accent-soft);
}
```

#### W2-C8 · `web/src/styles/global.css` — responsive

**Anchor:** inside `@media (max-width: 640px)`, the `.check-row, .evidence-proof-row, .metric-proof-row { ... }` rule (1559-1564).

**New code:**
```css
  .check-row,
  .evidence-proof-row,
  .metric-proof-row,
  .gate-check-row {
    grid-template-columns: 1fr;
    gap: 2px;
  }

  .gate-check-row .check-verdict,
  .gate-check-row .check-detail {
    grid-column: 1;
    text-align: left;
  }
```

### 4.4 W2 tests

| ID | File | Test | Asserts | Fails before |
|---|---|---|---|---|
| W2-T1 | `tests/test_presentation.py` | `test_filing_verification_projects_check_ids_and_only_v2_details` | `build_demo_db()`, `filing("0000950170-24-048288")`. Asserts `verification is not None`; `[c.check_id for c in checks] == ["V1","V4","V5","V2a","V2b","V2c","V2d"]`; `verdict == "PASS"`; V2c detail `== "rev=168088000000.0 gp=115856000000.0 oi=69916000000.0"`; V2a detail starts `"assets/liabilities/equity resolved to different period-ends"`; V2b detail starts `"cash tie-out compares the fiscal-year change"`; **every V1/V4/V5 row has `detail is None`** despite the persisted rows holding "all numbers matched" / "all citations verbatim" / "hygiene clean". Close the connection in a `finally`. | ✅ |
| **W2-T2** | `tests/test_presentation_fail_closed.py` | `test_only_deterministic_v2_details_cross_the_presentation_boundary` | **ADVERSARIAL.** Seeds one verified filing with a P1 analysis and three verification rows: V1 with detail `"orphan number 'UNVERIFIED_AUTHORED_4471' at pos 12"`, passing V4/V5, and V2a with detail `"A=100.0 L+E=<script>110.0\x00"` + `"x" * 300`. Asserts V1's projected detail `is None`; `"UNVERIFIED_AUTHORED_4471"` appears in neither `model_dump_json()` nor the `/api/filings/{accession}` response text; V2a's detail is not None, contains `"A=100.0"` and `"L+E="`, contains no `<`, `>`, `\x00`, and `len(...) <= 200`. Leave the existing parametrized sentinel test untouched. | ✅ |
| W2-T3 | `tests/test_review_fixes.py` | `test_v2_data_quality_results_are_never_marked_blocking` | Reuses `_bs(...)` (156-163). `data_quality_report(FactStore.from_companyfacts(_bs("2023-12-31","2023-12-31","2023-12-31",100,60,40)), sector_from_sic("7372"), form_type="10-K")`: every result `severity != "blocking"`, passing V2a has `verdict == "pass"` and `severity == "info"`. Then the imbalanced case (`...,100,60,50`): V2a still `verdict == "warn"`, `severity == "warning"`. | ✅ |
| W2-T4 | `web/src/components/VerificationBand.test.tsx` | `renders every persisted check with its machine id, human label, and verdict` | Fixture: `PASS_WITH_WARNINGS` with V1/V4/V5 PASS, V2a WARN with detail `"A=100.0 L+E=110.0"`, V2c SKIPPED_NOT_APPLICABLE, V9z PASS. Asserts codes present, V1 human label present, "Not applicable" present, overall pill "Passed with data-quality warnings", and the V9z row sits in the "Other recorded checks" group. | ✅ |
| W2-T5 | `web/src/components/VerificationBand.test.tsx` | `separates the blocking gate from non-blocking data quality and escapes all text` | Asserts `getByText("Blocking — a failure withholds the finding")`'s closest `.check-group` contains V1/V4/V5; `getByText("Non-blocking — reported, never a gate")`'s contains V2a/V2c; the V2a detail renders; the gate group contains no `.check-detail`. Then re-render with `detail: "<b>x</b>"` and assert `container.querySelector("b")` is null and the literal text is present. | ✅ |
| W2-T6 | `web/src/components/VerificationBand.test.tsx` | `renders nothing when there is no verification or no checks` | `verification={null}` and `{ verdict: "PASS", checks: [] }` both leave `container.firstChild` null. | ✅ (module does not exist) |
| W2-T7 | `web/src/pages/FilingPage.test.tsx` | `shows the deterministic gate under the outcome banner` | Extends `detail(outcome, overrides)` with a two-check `verification`. Asserts `findByText("What was checked")`; "V4" and its label render; `"rev=100.0 gp=60.0 oi=40.0"` renders; and in `document.body.textContent` the index of "What was checked" is greater than "Published with deterministic evidence checks" and less than "What changed". Second case: withheld fixture with the same override, band still appears. | ✅ |
| W2-T8 | `web/src/components/ProvenancePanel.test.tsx` | `renders duplicate check ids without a React duplicate-key error` | Certificate with two identical `V1` rows; expand the panel; assert `getAllByText("V1")` has length 2 and `vi.spyOn(console, "error")` was never called with `/same key/`. Restore the spy. | ✅ |

### 4.5 W2 acceptance criteria

1. Opening a verified filing shows "What was checked" directly under the outcome banner and above "What changed", listing V1/V4/V5 under "Blocking — a failure withholds the finding" and V2a–V2d under "Non-blocking — reported, never a gate". **`severity` is never rendered in the band.**
2. The V2c row on the demo MSFT 10-Q displays its deterministic detail; V1/V4/V5 display none.
3. `GET /api/filings/{accession}` returns `verification.checks[].detail` non-null only for ids matching `^V2[a-z]?$`. The pre-existing sentinel assertions in `tests/test_presentation_fail_closed.py` remain green.
4. No V2 result from `data_quality_report` carries `severity == "blocking"`.
5. **No gate logic changed:** `presentation/projection.py`, `verify/checks.py`, `verify/compiler.py`, `verify/presentation.py`, `presentation/canonical.py` untouched by this commit.
6. `digest/render.py` and `BriefView` unchanged — this workstream touches only `FilingDetailView` / `VerificationCheckView`.
7. `diff AGENTS.md CLAUDE.md` empty; §10 and §11 describe the new filing-UI scope.
8. `uv run pytest -q` green; `cd web && npm test -- --run && npm run typecheck && npm run build` green.
9. No `dangerouslySetInnerHTML`; check ids, labels, and details render as escaped React text.
10. Single `feat:` commit covering code, tests, and both doc mirrors.

### 4.6 W2 doc updates (both mirrors, same commit)

- **§10, final paragraph (lines 352-353).** Replace the two lines beginning "attempts have no certificate. The filing UI shows only the tool count," with six lines (each ≤100 columns):
  ```
  attempts have no certificate. The filing UI shows the persisted verification roll-up — one row per
  persisted check with its check id, a fixed human label, and its verdict — alongside the tool count,
  compact trace, dropped codes, and conditional download. V1/V4/V5 are shown as the blocking
  publication gate; V2 is shown as explicitly non-blocking data quality and is the only check family
  whose persisted detail string is projected, because V1/V4/V5 details quote model-authored text. Raw
  model output, gated-check details, and provider exceptions never cross the API boundary.
  ```
- **§10, the V2 bullet (325-326).** Replace with:
  ```
  - **V2 accounting identities:** non-blocking XBRL data-quality warnings only. They may populate open
    questions and are labelled non-blocking wherever they are displayed; every non-failing V2 result
    carries informational severity so no surface can present an accounting identity as a gate.
    Regenerating P1 cannot repair source accounting data.
  ```
- **§11**, insert after `- exact quotations link to HTTPS SEC pages;`: `- the filing page names each deterministic check that ran, its verdict, and whether it was blocking;`
- **`SYSTEM_DESIGN.md` §6**, append after line 280: `| Gate outcome is visible to the user | filing-detail verification band over persisted V1/V4/V5 + V2 rows |`

### 4.7 W2 risks

- **Detail-leak risk.** The value of `detail` depends on the check-id allow-list. If a future check family emits model-authored text under an id matching `^V2[a-z]?$`, it would be projected. Mitigated by the closed regex + charset filter + truncation, and pinned by W2-T2. **Any new check id must be reviewed against `_check_detail` before it is persisted.**
- **V2 misreading risk.** Showing an accounting identity next to the gate could suggest a V2 warning invalidates a finding. Mitigated by the separate group, the explicit note, omitting severity, and W2-C2. If user testing still shows confusion, collapse the V2 group behind a disclosure rather than removing the gate group.
- **Noise risk.** On most filings V2a/V2b/V2d are `skipped_not_applicable` (confirmed across all five demo filings), so the data-quality group is mostly "Not applicable" rows. This is honest. **Do NOT filter skipped rows out in `PresentationService`** — that would make the DTO lie about which checks ran. Adjust CSS or collapse in the component only.
- **Pre-existing data.** Databases written before W2-C2 still hold `V2c pass blocking` rows. The band never renders severity. **Do not backfill or rewrite persisted verification rows** — certificates are immutable by design.

---

## 5. W4 — First-run experience: demonstrate rigor instead of announcing absence

**Commit:** `feat: make the first-run brief demonstrate rigor instead of absence`
**Depends on:** W3 (BriefPage section structure). **W4-C7 blocked by Decision D2.**

### 5.1 Goal

A first-time visitor can always see the product's actual rigor within one click.

1. The bundled recorded demo dataset is served in **hosted** mode as well as local, from a throwaway in-memory database projected as the reserved local user, so "Open the sample brief" lands on 3 published filings, 4 evidence-backed findings, MSFT's two computed XBRL metrics, and a routine-filings result for AAPL.
2. The browser derives sample-mode chrome from `brief.sample_data` instead of the URL, so "Exit sample" can never appear on a non-sample page.
3. The zero-filing state stops claiming readiness: a three-step onboarding checklist driven by real counts offers exactly one next action, and replaces the header actions while showing.
4. Empty sections render clearly-labelled, un-linkable, un-tickered static specimens.
5. An analysis run that legitimately does nothing reports one of three typed, fixed reasons instead of "Analysis completed."

### 5.2 Why it matters

The deployed hosted instance is the entire first impression and today it proves nothing. The single escape hatch is a dead end in hosted mode (`app.py:616`), so the button navigates to the identical empty page minus the action buttons **plus** a nonsensical "Exit sample" control — three lines below copy that affirmatively promises the sample brief works (`AnalysisPanel.tsx:22`). The moment a user saves a model key with zero filings downloaded, `BriefPage.tsx:49` asserts "Filings are ready for analysis." and the run that follows says only "Analysis completed."

**Load-bearing premise, verified:** `_safe_item` (`web/jobs.py:141`) overwrites every caller-supplied `JobItem.message`. The "The newest supported filing is already complete…" string built at `app.py:948-951` therefore **never reaches the user**. Splitting that free-text message in `app.py` alone would change nothing — hence the typed reason vocabulary.

### 5.3 Changes

#### W4-C1 · `src/finwatch/preprocess/forms.py`

**Anchor:** module tail, after `form_family` (file is 25 lines).

**Change.** Give the three supported form families one definition. The literal `{"10-K","10-Q","8-K"}` is currently spelled twice (`pipeline/run.py:68`, `presentation/service.py:48` — the latter became `_SCOPED_FORMS` in W3-C2). Hoist it here, **not** into `pipeline/run.py`: `forms.py` is a leaf module (only import: `from __future__ import annotations`) that both already import from; importing `pipeline.run` from the presentation layer would drag `finwatch.llm.router` and the orchestrator into every brief request's import graph.

**New code:**
```python
ANALYZABLE_FORMS = frozenset({"10-K", "10-Q", "8-K"})
"""The three supported form families, shared by the pipeline runner, the brief
projection, and the web job runner so "supported filing" has exactly one definition.
Compare against ``base_form(...)`` so amendments stay in their base-form family."""
```

#### W4-C2 · `src/finwatch/pipeline/run.py`

**Change.** Delete the local `_ANALYZABLE_FORMS` (line 68) and import the shared one. `_ANALYZABLE_FORMS` has exactly three references repo-wide, all in this file.

**New code:**
```python
from finwatch.preprocess.forms import ANALYZABLE_FORMS, base_form

# ...

_TERMINAL_STATUS = frozenset({"verified", "analyzed"})
_MAX_PIPELINE_ATTEMPTS = 2

# line 87 becomes:
    if selected_form is not None and selected_form not in ANALYZABLE_FORMS:

# line 94 becomes:
            if base_form(filing.form_type) in ANALYZABLE_FORMS
```

#### W4-C3 · `src/finwatch/presentation/models.py`

**Anchor:** `class BriefView`, between `tracked_but_unanalyzed` and `disclaimer`.

**New code:**
```python
    withheld_filings: list[FilingDigestEntry] = Field(default_factory=list)
    tracked_but_unanalyzed: bool = False
    # Supported filings actually downloaded for this user's tickers, ignoring the
    # brief window. Onboarding must never claim a filing is ready to analyze when
    # nothing has been ingested.
    filings_synced: int = 0
    disclaimer: str = DISCLAIMER
    sample_data: bool = False
```

#### W4-C4 · `src/finwatch/presentation/service.py`

**Change.** Replace W3-C2's `_SCOPED_FORMS` constant with the shared `ANALYZABLE_FORMS` (extend the existing `finwatch.preprocess.forms` import — do **not** import `finwatch.pipeline.run`), add `_synced_filing_count` directly above `def brief(`, and populate `filings_synced`.

**New code:**
```python
from finwatch.preprocess.forms import ANALYZABLE_FORMS, base_form

# _scoped_filings, replacing the _SCOPED_FORMS reference:
            and base_form(filing.form_type) in ANALYZABLE_FORMS

# new method, placed directly above `def brief(`:
    def _synced_filing_count(self) -> int:
        """Count supported filings actually downloaded for this user's tickers.

        This deliberately ignores the brief window: onboarding asks whether anything
        has been ingested at all, not whether it falls inside the current period.
        """
        tracked_ciks = set(self.repo.list_tracked_ciks(self.user_id))
        return sum(
            1
            for filing in self.repo.list_filings()
            if filing.cik in tracked_ciks
            and base_form(filing.form_type) in ANALYZABLE_FORMS
        )

# brief() return block:
            tracked_but_unanalyzed=bool(tracked and not analyzed and not outside_window),
            filings_synced=self._synced_filing_count(),
            sample_data=sample_data,
        )
```

Delete the now-unused `_SCOPED_FORMS` module constant.

#### W4-C5 · `src/finwatch/digest/render.py`

**Anchor:** the `if brief.tracked_but_unanalyzed:` block (180-186).

**Change.** §11 requires `digest/render.py` to serialize the same `BriefView`. Mirror the new field: an unsynced watchlist and a synced-but-unanalyzed watchlist get different next steps. Still a pure serialization — no extra DB access, no new imports.

**New code:**
```python
    if brief.tracked_but_unanalyzed:
        # Mirror the browser onboarding checklist: an unsynced watchlist and a synced
        # but unanalyzed one are different next steps, and neither is the filing's fault.
        lines.extend(
            [
                (
                    "_Tracked companies have no synced filings yet. Sync filings first._"
                    if brief.filings_synced == 0
                    else "_Tracked companies have synced filings but no analyzed filing "
                    "yet. Run analysis to begin._"
                ),
                "",
            ]
        )
```

#### W4-C6 · `src/finwatch/web/jobs.py`

**Anchor:** `_STAGE_LABELS` (ends line 27); `_safe_message` (30-51); `class JobItem` (54-60); `_safe_item` (131-145).

**Change.** Introduce a typed reason vocabulary: the caller selects a code, the registry allowlists it and maps it to fixed text. Keeps §12 ("fixed user-safe messages only") intact while making the three no-op outcomes distinguishable. `message` gains a `""` default because the new call site supplies only a reason.

**New code:**
```python
# inserted after the _STAGE_LABELS dict:
# Fixed explanations for a run that legitimately did nothing. Callers select a code;
# caller-supplied display text never reaches the API (AGENTS.md §12).
_JOB_REASONS = {
    "no_filings_synced": (
        "No SEC filings have been synced yet. Sync filings, then run analysis."
    ),
    "form_not_synced": (
        "No filing of the selected type has been synced. "
        "Sync filings or choose another filing type."
    ),
    "newest_already_analyzed": (
        "The newest supported filing has already been analyzed. "
        "There is nothing new to analyze right now."
    ),
}


def _safe_message(
    kind: JobKind, *, state: str, stage: str | None, reason: str | None = None
) -> str:
    """Return only fixed text; provider and exception strings are never display data."""
    if stage in _STAGE_LABELS:
        label = _STAGE_LABELS[stage]
        return {
            "queued": f"{label} is queued.",
            "running": f"{label}…",
            "completed": f"{label} complete.",
            "skipped": f"{label} was not needed.",
            "failed": f"{label} could not be completed.",
        }.get(state, f"{label} could not be completed.")
    if reason in _JOB_REASONS:
        return _JOB_REASONS[reason]
    if kind == "sync":
        return (
            "Filings and verified metrics synced."
            if state == "completed"
            else "Filing sync could not be completed."
        )
    return (
        "Analysis completed."
        if state == "completed"
        else "Analysis could not be completed."
    )


class JobItem(BaseModel):
    key: str
    state: str
    message: str = ""
    verdict: str | None = None
    stage: str | None = None
    reason: str | None = None
    diagnostics: dict = Field(default_factory=dict)


# _safe_item body:
    def _safe_item(self, job_id: str, item: JobItem) -> JobItem:
        kind = self._jobs[job_id].kind
        state = item.state if item.state in _SAFE_ITEM_STATES else "failed"
        stage = item.stage if item.stage in _STAGE_LABELS else None
        verdict = item.verdict if item.verdict in _SAFE_VERDICTS else None
        reason = item.reason if item.reason in _JOB_REASONS else None
        return item.model_copy(
            update={
                "state": state,
                "stage": stage,
                "verdict": verdict,
                "reason": reason,
                "message": _safe_message(
                    kind, state=state, stage=stage, reason=reason
                ),
                "diagnostics": {},
            },
            deep=True,
        )
```

#### W4-C7 · `src/finwatch/web/app.py` — **BLOCKED BY DECISION D2**

**Anchor:** `principal_for` (469-470); the four `demo = demo and not remote` lines (616, 641, 656, 762); the four `PresentationService(repo, user_id=principal.user_id)` constructions (629, 643, 658, 765); the local import block in `analysis_work` (875-884); the `if filing is None:` branch (941-953).

**Change (a).** Delete all four `demo = demo and not remote` lines (616 with its `LOW-6` comment, 641, 656, 762). `grep -n 'demo and not remote' src/finwatch/web/app.py` must return nothing afterwards. **Leave `repo_context` (461-467) and the `from finwatch.demo import DEMO_SINCE, build_demo_db` import at line 20 EXACTLY as they are** — `build_demo_db()` measures ~25 ms (5 runs, warm), so no caching, snapshot, or serialization layer is warranted or wanted.

**Change (b).** The demo database's watchlist rows are written by `Repo.track_company` with its default `user_id=LOCAL_USER_ID`, so a hosted participant's own id would project zero tracked tickers against it. Add `sample_scope` after `principal_for` and use it in all four demo-capable endpoints. `LOCAL_USER_ID` is already imported at line 19.

**Change (c).** Replace the discarded free-text no-filing message with typed reason codes.

**New code:**
```python
# new helper, directly after principal_for (469-470):
    def sample_scope(principal: RequestPrincipal, demo: bool) -> str:
        """Project sample requests against the bundled public dataset only.

        The demo database is a throwaway in-memory build of public SEC fixtures whose
        watchlist belongs to the reserved local user, so a participant's own scope must
        not be applied to it — and their private data lives in another database entirely.
        """
        return LOCAL_USER_ID if demo else principal.user_id

# brief endpoint (the `demo = demo and not remote` line is deleted):
            return PresentationService(
                repo, user_id=sample_scope(principal, demo)
            ).brief(
                since=since_value,
                sample_data=demo,
            )

# filing_detail (line 643):
            result = PresentationService(
                repo, user_id=sample_scope(principal, demo)
            ).filing(accession)

# filing_certificate (line 658):
            result = PresentationService(
                repo, user_id=sample_scope(principal, demo)
            ).certificate(accession)

# company_metrics (line 765):
            result = PresentationService(
                repo, user_id=sample_scope(principal, demo)
            ).metrics(ticker, as_of=selected_date)

# analysis_work local imports (880-884), add the forms import:
            from finwatch.pipeline.run import (
                build_orchestrator,
                newest_filing_to_analyze,
                process_filing,
            )
            from finwatch.preprocess.forms import ANALYZABLE_FORMS, base_form

# analysis_work, the `filing is None` branch:
                if filing is None:
                    # Never blame the data for a skipped step: say which of the three
                    # no-op cases actually happened (AGENTS.md §3).
                    scope_ciks = [cik] if cik is not None else sorted(tracked_ciks)
                    synced = [
                        candidate
                        for scope_cik in scope_ciks
                        for candidate in repo.list_filings(scope_cik)
                        if base_form(candidate.form_type) in ANALYZABLE_FORMS
                    ]
                    selected_form = base_form(form_type) if form_type else None
                    if not synced:
                        reason = "no_filings_synced"
                    elif selected_form is not None and not any(
                        base_form(candidate.form_type) == selected_form
                        for candidate in synced
                    ):
                        reason = "form_not_synced"
                    else:
                        reason = "newest_already_analyzed"
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=ticker.upper() if ticker else "portfolio",
                            state="completed",
                            reason=reason,
                        ),
                    )
```

> **W5-C13 also modifies the `filing_detail` route** (adds `sample_data=demo`). Land W4 first; W5-C13 amends the call built here.

#### W4-C8 · `web/src/types.ts`

**New code** (mirroring the W1+W3 shapes, adding `filings_synced` after `tracked_but_unanalyzed`, and `reason` after `stage`):
```typescript
export interface Brief { period: { covered_label: string; filings_in_window: number; analyzed_filings: number; published_filings: number; withheld_filings: number; filings_tracked_total: number; outside_window: string | null }; tracked_tickers: string[]; answer: string; answer_posture: Posture | null; filings: FilingDigestEntry[]; gate_removed_filings: FilingDigestEntry[]; verified_numbers: IssuerMetrics[]; open_questions: string[]; reviewed_filings: FilingDigestEntry[]; withheld_filings: FilingDigestEntry[]; tracked_but_unanalyzed: boolean; filings_synced: number; disclaimer: string; sample_data: boolean }
export interface Job { id: string; kind: "sync" | "analysis"; state: "queued" | "running" | "completed" | "partial" | "failed"; created_at: string; items: { key: string; state: string; message: string; verdict: string | null; stage: string | null; reason: string | null; diagnostics: Record<string, unknown> }[]; error: string | null }
```

#### W4-C9 · `web/src/components/OnboardingChecklist.tsx` (new file)

**Change.** A three-state checklist driven by real counts. It states what is done, what is not, and offers exactly ONE next action. `BriefPage` renders it only when at least one ticker is tracked (the existing zero-ticker `empty-invitation` at `BriefPage.tsx:48` stays), so no zero-ticker branch is needed. All text is escaped React text; no trade action, no price target.

**New code:**
```tsx
export function OnboardingChecklist({
  trackedCount,
  filingsSynced,
  analysisConfigured,
  onSync,
  onAnalyze,
}: {
  trackedCount: number;
  filingsSynced: number;
  analysisConfigured: boolean;
  onSync: () => void;
  onAnalyze: () => void;
}) {
  const steps = [
    {
      key: "track",
      done: trackedCount > 0,
      label: "Track a ticker",
      detail: trackedCount === 1 ? "1 company tracked" : `${trackedCount} companies tracked`,
    },
    {
      key: "sync",
      done: filingsSynced > 0,
      label: "Sync filings from SEC EDGAR",
      detail: filingsSynced > 0
        ? `${filingsSynced} supported filing${filingsSynced === 1 ? "" : "s"} downloaded`
        : "No filings downloaded yet, so there is nothing to analyze",
    },
    {
      key: "model",
      done: analysisConfigured,
      label: "Connect an analysis model",
      detail: analysisConfigured
        ? "Model and provider key configured"
        : "Only AI-selected changes need a model; verified numbers never do",
    },
  ];
  const next = steps.find(step => !step.done);
  return <section className="onboarding">
    <div className="onboarding-copy"><p className="section-kicker">Set up</p><h2>Three steps to your first verified brief.</h2></div>
    <ol className="onboarding-steps">
      {steps.map((step, index) => <li className={`onboarding-step${step.done ? " done" : ""}${step.key === next?.key ? " next" : ""}`} key={step.key}>
        <span className="onboarding-mark" aria-hidden="true">{step.done ? "✓" : String(index + 1).padStart(2, "0")}</span>
        <div><strong>{step.label}</strong><p>{step.detail}</p></div>
        <span className="sr-only">{step.done ? "step complete" : "step not complete"}</span>
      </li>)}
    </ol>
    <div className="onboarding-actions">
      {next?.key === "sync" && <button className="button primary" onClick={onSync}>Sync filings from SEC</button>}
      {next?.key === "model" && <a className="button primary" href="/settings">Connect an analysis model</a>}
      {!next && <button className="button primary" onClick={onAnalyze}>Analyze the newest filing</button>}
    </div>
  </section>;
}
```

#### W4-C10 · `web/src/components/ExampleSpecimen.tsx` (new file)

**Change.** Two static specimens showing the real shape of a verified finding and a verified metric, reusing the real classes so the layout shown is genuinely the product's.

> **CONTRACT — seven independent non-mistakability properties. Preserve every one:** (1) a persistent visible chip reading "Example — not your data" plus the same string as the container `aria-label`; (2) `data-specimen="true"` on the container; (3) **NO `<a>` anywhere inside** — the SEC citation is an inert `<span>`; (4) no ticker, no company name, no accession number, and no date — the metric date literally reads `YYYY-MM-DD`; (5) the quotation is self-describing placeholder prose, never a fabricated issuer statement; (6) the badge reads `AI-selected · example`, which is not a valid `Severity`, hand-written rather than rendered through `SeverityBadge`; (7) `BriefPage` renders these ONLY while the onboarding checklist is showing.

**New code:**
```tsx
import type { ReactNode } from "react";

const SPECIMEN_LABEL = "Example — not your data";

function SpecimenFrame({ caption, children }: { caption: string; children: ReactNode }) {
  return <aside className="specimen" data-specimen="true" aria-label={SPECIMEN_LABEL}>
    <p className="specimen-label"><span className="specimen-tag">{SPECIMEN_LABEL}</span>{caption}</p>
    {children}
  </aside>;
}

export function ExampleFindingSpecimen() {
  return <SpecimenFrame caption="What a published finding looks like once its quotation has been matched to the filing character for character.">
    <article className="finding">
      <div className="finding-heading"><h3>Example headline: a qualitative change the model selected</h3><span className="pill neutral">AI-selected · example</span></div>
      <div className="finding-evidence"><div className="evidence">
        <blockquote className="quote">This placeholder stands in for the exact sentence RipplX copies out of the filing.</blockquote>
        <p className="citation-line"><span className="citation-meta">section key · character offsets · section hash</span><span className="citation faint">Example only — no SEC link</span></p>
      </div></div>
    </article>
  </SpecimenFrame>;
}

export function ExampleMetricSpecimen() {
  return <SpecimenFrame caption="What a verified number looks like: a versioned Python formula over SEC XBRL facts, never a model output.">
    <div className="table-scroll"><table className="metric-table">
      <caption className="sr-only">Example deterministic SEC XBRL metric result</caption>
      <thead><tr><th>Metric</th><th>Value</th><th>Method &amp; source</th><th>Status</th></tr></thead>
      <tbody><tr>
        <td>Revenue growth</td>
        <td>example value</td>
        <td><code className="formula">revenue_growth.v1</code><span className="metric-source">example computation · computed as of YYYY-MM-DD</span></td>
        <td><span className="trust computed">✓ Computed</span></td>
      </tr></tbody>
    </table></div>
  </SpecimenFrame>;
}
```

#### W4-C11 · `web/src/components/AnalysisPanel.tsx`

**Change.** The "sample brief still works without one" claim becomes TRUE everywhere once W4-C7 ships, so keep the button and make the copy concrete. Rename user-facing "latest" to "newest" to match backend vocabulary. The `FilingType` union value `"latest"` is unchanged — display text only.

**New code:**
```tsx
// line 22
      <p>RipplX needs a model name and provider key to read filing text. Tracking, SEC syncing, and verified XBRL numbers work without one — and the bundled sample brief shows the full evidence path with no key at all.</p>

// line 25
        {onDemo && <button className="button" onClick={onDemo}>Open the sample brief</button>}

// line 30
  const selectedLabel = formType === "latest" ? "newest filing" : `newest ${formType}`;

// line 34
      <div><strong>Evidence-first analysis</strong><p>RipplX analyzes only the newest filing in the family you choose — it never falls back to older filings. Exact SEC quotations and deterministic checks gate every published finding.</p></div>
```

#### W4-C12 · `web/src/pages/BriefPage.tsx`

**Change.** (1) Derive sample chrome from `brief.sample_data`, never from the URL — `demo` (line 17) stays ONLY as the fetch parameter for `load` (19-20). (2) Invert the CTA hierarchy: Sync primary, Analyze secondary, plus a legend that states the newest-only contract. (3) Replace the `guidance-note` block with `OnboardingChecklist` and **suppress the header actions while onboarding is showing** so exactly one next action is offered. (4) Render specimens in sections 01/03 while onboarding is showing. Keep the zero-ticker `empty-invitation` block exactly as it is.

**The now-unused `.guidance-note` CSS rules in `global.css` must be deleted in the same commit.** Confirm with `grep -rn 'guidance-note' web/src` returning only style hits before removing them. Also remove `guidance-note` from the 640px stacking selector list.

**New code:**
```tsx
// add to imports
import { ExampleFindingSpecimen, ExampleMetricSpecimen } from "../components/ExampleSpecimen";
import { OnboardingChecklist } from "../components/OnboardingChecklist";

// after `const brief = resource.data;`
  const sample = brief.sample_data;
  const trackedTickers = brief.tracked_tickers;
  // Onboarding is the cold-start state: tickers exist but no filing has been analyzed.
  const showOnboarding = !sample && trackedTickers.length > 0 && brief.tracked_but_unanalyzed;
  const hasComputedMetrics = brief.verified_numbers.some(issuer => issuer.rows.length > 0);

// sample banner
    {sample && <div className="notice neutral">Sample brief · bundled public SEC filings run through the real pipeline with recorded model output. This is not your watchlist.</div>}

// header actions (inside <header className="brief-header">)
<div className="actions">{!sample && !showOnboarding && trackedTickers.length > 0 && <><button className="button primary" onClick={() => start("sync")}><span aria-hidden="true">↻</span> Sync filings from SEC</button><button className="button" onClick={() => navigate("?panel=analysis")}>Analyze newest filing <span aria-hidden="true">→</span></button></>} {sample && <button className="button" onClick={() => navigate("/brief")}>Exit sample</button>}</div>

// hero copy: add one line after the existing hero-note
      <div className="hero-copy"><span className="hero-label">Executive read</span><p className="answer-hero">{brief.answer}</p><p className="hero-note">AI-selected significance · publication gated by exact-evidence checks</p><p className="hero-note action-legend">Sync downloads new SEC filings and recomputes verified numbers — no model key needed. Analyze reads the newest supported filing for changes; RipplX never revisits older filings.</p></div>

// replaces the whole `{brief.tracked_but_unanalyzed && <section className="guidance-note">…}` line
    {showOnboarding && <OnboardingChecklist trackedCount={trackedTickers.length} filingsSynced={brief.filings_synced} analysisConfigured={bootstrap.analysis_configured} onSync={() => start("sync")} onAnalyze={() => navigate("?panel=analysis")} />}

// section 01 body, replacing the `brief.filings.length ? … : <div className="empty-state">…` expression
{brief.filings.length ? brief.filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} />) : showOnboarding ? <ExampleFindingSpecimen /> : <div className="empty-state"><span aria-hidden="true">—</span><div><strong>No evidence-backed changes selected</strong><p>The analyzed filings were routine or did not produce a finding that cleared the gate.</p></div></div>}

// section 03 (SEC XBRL): append after the issuer blocks, still inside the <section>
{showOnboarding && !hasComputedMetrics && <ExampleMetricSpecimen />}
```

#### W4-C13 · `web/src/styles/global.css`

**Anchor:** immediately after `.empty-state.compact { padding-block: 8px; }` (746-748), before `.filing-detail-hero {` (750). **Also delete the four `.guidance-note` rules.**

**New code:**
```css
.empty-state.compact {
  padding-block: 8px;
}

.action-legend {
  max-width: 62ch;
  margin-top: 6px;
  color: var(--color-muted);
}

.onboarding {
  margin-top: 34px;
  padding: 26px 0 8px;
  border-top: var(--border-hair);
}

.onboarding-copy h2 {
  margin: 6px 0 18px;
  font-size: var(--text-h2);
}

.onboarding-steps {
  display: grid;
  gap: 2px;
  margin: 0 0 22px;
  padding: 0;
  list-style: none;
}

.onboarding-step {
  display: flex;
  gap: 16px;
  align-items: baseline;
  padding: 14px 16px;
  color: var(--color-muted);
  background: var(--color-panel-alt);
  border-left: 3px solid transparent;
}

.onboarding-step.done {
  color: var(--color-body);
  border-left-color: var(--color-accent);
}

.onboarding-step.next {
  background: var(--color-accent-wash);
  border-left-color: var(--color-accent-strong);
}

.onboarding-step strong {
  display: block;
  color: var(--color-ink);
}

.onboarding-step p {
  margin: 3px 0 0;
  font-size: var(--text-body-sm);
}

.onboarding-mark {
  min-width: 22px;
  color: var(--color-accent-strong);
  font-family: var(--font-mono);
  font-size: var(--text-caption);
}

.onboarding-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.specimen {
  margin: 18px 0 8px;
  padding: 20px 22px 12px;
  background: var(--color-panel-alt);
  border: 1px dashed var(--color-hairline-strong);
  border-radius: var(--radius-sm);
}

.specimen-label {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin: 0 0 14px;
  color: var(--color-muted);
  font-size: var(--text-body-sm);
}

.specimen-tag {
  padding: 3px 9px;
  color: var(--color-warn);
  background: var(--color-warn-wash);
  border: 1px dashed var(--color-warn);
  border-radius: var(--radius-pill);
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  letter-spacing: var(--tracking-badge);
  text-transform: uppercase;
}

.specimen .quote {
  font-size: clamp(18px, 2.4vw, 24px);
}

.filing-detail-hero {
```

> **W6 note:** W6-C1 deletes `--text-h2` and `--tracking-badge` and W6-C5 removes every `font-size: clamp(` from `global.css`. When W6 lands, `.onboarding-copy h2` becomes `var(--text-serif-lg)`, `.specimen-tag`'s `letter-spacing` becomes `var(--tracking-kicker)`, and `.specimen .quote` becomes `var(--text-serif-md)`.

### 5.4 W4 tests

| ID | File | Test | Asserts | Fails before |
|---|---|---|---|---|
| W4-T1 | `tests/test_presentation.py` | `test_brief_counts_synced_filings_for_onboarding` | Empty DB; upsert+track `ZZZ`. `brief()` → `filings_synced == 0`, `tracked_but_unanalyzed is True`. Add one 10-K and one `S-1`; re-run → `filings_synced == 1`. With `build_demo_db()` → `brief(since=DEMO_SINCE).filings_synced == 5`. | ✅ |
| W4-T2 | `tests/test_digest.py` | `test_digest_separates_unsynced_from_unanalyzed_watchlists` | Minimal `BriefView(..., tracked_but_unanalyzed=True, filings_synced=0)`: markdown contains "no synced filings yet", not "no analyzed filing". With `filings_synced=3`: contains "no analyzed filing yet", not "no synced filings". | ✅ |
| W4-T3 | `tests/test_web_security.py` | `test_remote_sample_brief_serves_only_bundled_public_data` | **REPLACES `test_demo_parameter_is_ignored_in_remote_mode` (202-210)** — delete it and its LOW-6 comment. `_remote_app`, `_login`. `alice.get("/api/brief?demo=true")` → 200, `sample_data is True`, `tracked_tickers == ["AAPL","DPLS","MSFT","TWKS"]`, `len(filings) == 3`, total findings == 4, `filings[0]["findings"][0]["evidence"][0]["edgar_url"].startswith("https://www.sec.gov/")`. Then track `QQQZ` for alice in the operational DB and assert (a) the demo response still returns the four demo tickers without `QQQZ`, and (b) `alice.get("/api/brief").json()["tracked_tickers"] == ["QQQZ"]`. **This is the isolation property that makes hosted demo safe.** | ✅ |
| W4-T4 | `tests/test_web.py` | `test_analysis_job_explains_why_no_filing_was_analyzed` | Three sub-cases, each with `SEC_USER_AGENT`, `FINWATCH_MODEL`, a session provider key, `POST /api/jobs/analyze`, and a **bounded** poll (`for _ in range(200): … time.sleep(0.005)`). No network is reached (`newest_filing_to_analyze` returns None before any fetch). (a) zero companies → `reason == "no_filings_synced"` and its fixed message. (b) demo DB (all terminal) → `"newest_already_analyzed"`. (c) one non-terminal 10-Q, posting `{"form_type": "10-K"}` → `"form_not_synced"`. | ✅ |
| W4-T5 | `tests/test_web.py` | `test_job_items_never_display_caller_supplied_text` | **ADVERSARIAL.** `registry.add_item(job_id, JobItem(key="k", state="completed", message="raw provider blew up: sk-live-123", reason="totally_unknown_code"))`. Asserts `message == "Analysis completed."`, `reason is None`, `"sk-live-123"` absent from `view.model_dump_json()`. | ✅ (reason half) |
| W4-T6 | `web/src/pages/BriefPage.test.tsx` | `renders a three-step onboarding checklist driven by real counts` | Payload: one ticker, `filings_synced: 0`, `tracked_but_unanalyzed: true`, empty lists, `sample_data: false`; bootstrap `analysis_configured: true`. Asserts "No filings downloaded yet, so there is nothing to analyze"; `.onboarding-actions` textContent exactly "Sync filings from SEC"; `queryByText("Analyze the newest filing")` null; `queryByText("Filings are ready for analysis.")` null; `getAllByRole("button", { name: "Sync filings from SEC" })` length **1**. | ✅ |
| W4-T7 | `web/src/pages/BriefPage.test.tsx` | `labels cold-start specimens as examples and never links them` | `querySelectorAll("[data-specimen]")` length 2; `querySelectorAll("[data-specimen] a")` length 0; every specimen's `aria-label === "Example — not your data"`; joined textContent contains "YYYY-MM-DD", does not match `/\d{4}/`, does not contain the ticker. Second payload with `tracked_but_unanalyzed: false` and one published filing → 0 specimens. | ✅ |
| W4-T8 | `web/src/pages/BriefPage.test.tsx` | `derives sample chrome from the payload rather than the URL` | Render at `/brief?demo=1` with `sample_data: false`, one published filing: `queryByRole("button", { name: "Exit sample" })` null, both action buttons present. Then `sample_data: true`: "Exit sample" present, neither action button. | ✅ |

### 5.5 W4 acceptance criteria

1. Hosted `GET /api/brief?demo=true` returns `sample_data: true` with 3 published filings, 4 findings, tracked tickers `[AAPL, DPLS, MSFT, TWKS]`, MSFT's two computed metrics, and the routine result for AAPL; the same participant's `GET /api/brief` is unchanged and contains none of the demo tickers.
2. `grep -n 'demo and not remote' src/finwatch/web/app.py` returns nothing; `repo_context` is untouched; all four demo-capable endpoints project with `sample_scope(principal, demo)`.
3. `grep -rn '_ANALYZABLE_FORMS' src tests` returns nothing, and `grep -rn '"10-K", "10-Q", "8-K"' src` matches only `src/finwatch/preprocess/forms.py`.
4. With one ticker tracked and zero filings synced, the brief shows the three-step checklist with "Sync filings from SEC" as the **single** primary action anywhere on the page, and never "Filings are ready for analysis".
5. Cold-start sections render exactly one `[data-specimen]` block each, containing zero `<a>` elements, the aria-label, the visible tag, and no ticker/accession/real date.
6. Once at least one filing is analyzed, "Sync filings from SEC" is the `.button.primary` and "Analyze newest filing" is secondary; both verbs are defined in one legend that states RipplX never revisits older filings.
7. An analyze run with nothing to do reports one of exactly three fixed messages; no caller-supplied text and no unknown reason code reaches `/api/jobs/{id}`.
8. `grep -rn 'guidance-note' web/src` returns nothing.
9. `uv run pytest -q` green; `cd web && npm test -- --run && npm run typecheck && npm run build` green.
10. `cmp AGENTS.md CLAUDE.md` exits 0.

### 5.6 W4 doc updates (both mirrors, same commit)

- **§3 → "Launch scheduling and retries"**, appended to the bullet ending "It never falls through to an older filing.": *"When a requested run has nothing to do, it reports exactly one of three fixed reasons: no supported filing has been synced, no filing of the selected form family has been synced, or the newest supported filing has already been analyzed. Job items never carry caller-supplied display text."*
- **§5**, new paragraph after "The browser app is the launch surface.": *"The browser also serves a read-only sample brief at `?demo=1` in both local and hosted mode. It is built from bundled public SEC fixtures by the real pipeline with recorded model output, lives in a throwaway in-memory database created per request, and is always projected as the reserved local user so it can never read or mix with a participant's own data."*
- **§11**, new bullet: *"the brief reports how many supported filings have actually been synced, so an unsynced watchlist and a synced-but-unanalyzed watchlist get different next steps in both the browser and `digest/render.py`;"*
- **§12**, replace "The single owner-tagged job registry strips diagnostics, allowlists verdicts/stages, and returns only fixed user-safe failure messages;" with: *"…strips diagnostics, allowlists verdicts, stages, and a closed set of typed no-op reason codes, and returns only fixed user-safe failure messages; caller-supplied item text is always discarded;"*

### 5.7 W4 risks

- Serving the demo in hosted mode reverses a documented decision (the `LOW-6` comment at `app.py:616` and `tests/test_web_security.py:202-210` — the only two LOW-6 references in the repo). **Delete both together with the behavior change**; do not leave contradictory guidance behind.
- `sample_scope` hard-codes `LOCAL_USER_ID` for demo requests. If a future change lets `repo_context(demo=True)` yield the operational database, that helper would silently read the reserved local user's real watchlist. **Keep the demo branch of `repo_context` pointing at `build_demo_db()` and nothing else.**
- `reason` on `JobItem` could become a free-text channel if a future caller sets it from an exception. The allowlist in `_safe_item` plus W4-T5 are the guard; do not relax either.
- The specimens are static marketing-adjacent content inside a trust-first product. **If any of the seven non-mistakability properties is dropped, the change becomes actively harmful.**
- `build_demo_db()` runs on every sample request (~25 ms measured). Fine at alpha volume, but unbounded work on a query parameter; if the corpus grows materially, revisit with a measurement, not a guess.
- The three job-reason sub-cases spin real background threads. Bound every poll loop and assert on the terminal state so a hang surfaces as a failed assertion, not a stuck CI job.

---

## 6. W5 — Outcome encoding and copy precision

**Commit:** `fix: encode publication outcomes and metric wording the way the system behaves`
**Depends on:** W3 (`_validated_metrics`, `MetricTable` summary prop).

### 6.1 Goal

- A `partial` publication — the designed happy path of per-finding fail-closed — renders with the verified glyph and colours plus a counted headline, instead of sharing the amber alarm treatment with `withheld`.
- The deterministic ✓ on SEC-XBRL rows is never suppressed because the LLM failed (`showComputedMark` deleted outright).
- Every drop code the compiler or the Skeptic can emit has a plain-language label, guarded by an AST-based invariant test.
- The evidence citation link is labelled for what it actually opens.
- Share-count direction stops printing certified wording for an uncertified verdict.
- The at-most-three cap and the fixed six-metric catalog are stated on the surfaces that show them; the company-row compressed read is built from validated `MetricResult.components`; the "no open questions" empty state stops claiming a review happened; job state renders through a typed gloss map; demo filing pages carry the server-authoritative sample-data label.

### 6.2 Why it matters

Rendering `partial` as an alarm tells the user the mechanism the project is proudest of is a malfunction. Hiding the ✓ on an XBRL row when the LLM failed tells the user the number depends on the model — and the identical row on the company page shows "✓ Computed", so the product contradicts itself one click apart. `DUPLICATE_EVIDENCE` and `EMPTY_HEADLINE` are both live in `verify/compiler.py` and both render as a bare `<abbr>` with `title={undefined}`. "Open exact SEC source" on a link to the EDGAR filing-*index* directory burns the user's single act of independent verification. And the share-count row asserts, in the exact wording reserved for a proven direction, the same claim the compiler drops as `METRIC_DIRECTION_UNAVAILABLE`.

### 6.3 Changes

#### W5-C1 · `web/src/pages/FilingPage.tsx` — partial outcome

**Anchor:** immediately after `researchOutcomeLabel(...)` (29-40), plus the outcome banner (73-76).

**Change.** `partial` is the designed per-finding fail-closed success path. Give it the verified glyph and a counted headline built from `filing.findings.length` and `research.dropped_findings.length`. No API change. `researchOutcomeLabel` stays live and stays the exhaustive switch.

**The `<small>` secondary line is NOT changed** — it stays `{reasonLabel}`. Concatenating it would break the existing `it.each` assertion at `FilingPage.test.tsx:83-91` (exact-string matcher) for no user benefit. And `outcomeHeadline` falls back to `researchOutcomeLabel(outcome)` when `droppedCount === 0`, because the existing `detail("partial")` fixture has `dropped_findings: []` and would otherwise render "1 finding published, 0 findings removed".

**New code:**
```tsx
const VERIFIED_OUTCOMES = new Set<NonNullable<FilingDetail["research"]>["outcome"]>(["published", "partial"]);

/** A partial publication is the per-finding gate working, so it leads with counts. */
export function outcomeHeadline(
  outcome: NonNullable<FilingDetail["research"]>["outcome"],
  publishedCount: number,
  droppedCount: number,
): string {
  // `partial` implies a drop (llm/harness.py picks it only when findings were pruned);
  // the zero case is defensive so the banner can never read "0 findings removed".
  if (outcome !== "partial" || droppedCount === 0) return researchOutcomeLabel(outcome);
  const published = `${publishedCount} ${publishedCount === 1 ? "finding" : "findings"} published`;
  const removed = `${droppedCount} ${droppedCount === 1 ? "finding" : "findings"} removed by the evidence gate`;
  return `${published}, ${removed}`;
}

// ...inside FilingPage, replacing the banner block:
    {research && <section className={`outcome-banner ${research.outcome}`} aria-label="Publication outcome">
      <span className="outcome-glyph" aria-hidden="true">{VERIFIED_OUTCOMES.has(research.outcome) ? "✓" : "!"}</span>
      <div><p>{outcomeHeadline(research.outcome, filing.findings.length, research.dropped_findings.length)}</p><small>{reasonLabel}</small></div>
    </section>}
```

#### W5-C2 · `web/src/pages/FilingPage.tsx` — sample label, ✓, caps

**Anchor:** the `return <main className="page filing-page">` opening (66-67, `filing-page` removed by W6-C8); the changes-section caption (81); the numbers-section `MetricTable` line (87, as amended by W3-C13).

**Change.** (a) Render the sample-data notice driven by the new server-authoritative `detail.sample_data` (W5-C12/C13/C14). `ProvenancePanel` is suppressed when `demo` is true, so this is the page's only sample label. The flag comes from the server, not the local query string. (b) **Delete `showComputedMark`** — an XBRL row's ✓ never depended on the model. (c) State the at-most-three cap and add the missing six-metric caption. Note `withheld` (line 60) remains used by lines 80 and 82 — do not delete it.

**New code:**
```tsx
  return <main className="page">
    {detail.sample_data && <div className="notice neutral">Sample data · generated by the bundled recorded pipeline</div>}
    <button className="button ghost back-button" onClick={() => navigate(-1)}>← Back</button>

// ...changes-section caption:
      <p className="metric-caption">The model selects significance and publishes at most three findings per filing. Deterministic checks prove that every displayed quotation matches the filing; they do not decide what is important to you.</p>

// ...numbers section: caption added above the table, showComputedMark removed:
      <p className="metric-caption">The same six metrics are computed for every issuer by versioned deterministic formulas, directly from SEC XBRL facts—never from the language model.</p>
      {detail.verified_numbers && detail.verified_numbers.rows.length > 0 ? <MetricTable rows={detail.verified_numbers.rows} summary={detail.verified_numbers.summary} /> : <p className="empty-line">{detail.verified_numbers?.empty ?? "No SEC XBRL metric has been computed for this issuer yet."}</p>}
```

#### W5-C3 · `web/src/styles/global.css` — outcome colour split

**Anchor:** `.outcome-banner.published { ... }` (786-789, immediately after the unqualified `.outcome-banner` block at 775-784) and `.outcome-word.published { ... }` (853-855).

**Change.** Give `partial` the verified treatment alongside `published`; leave the unqualified rule (amber) as the `metrics_only` default; give `withheld` the strongest treatment via `--color-critical` plus a thicker left rule. Mirror on the `.outcome-word` chip. **No new tokens.** Contrast verified: `#9c4437` on `#f8eee4` is 5.56:1, clearing AA; there is no dark theme in `web/src/styles`.

> W1-C12 adds `.outcome-banner.not-analyzed`; that rule is unaffected by this split.

**New code:**
```css
.outcome-banner.published,
.outcome-banner.partial {
  color: var(--color-verified);
  background: var(--color-accent-wash);
}

.outcome-banner.withheld {
  color: var(--color-critical);
  background: var(--color-warn-wash);
  border-left-width: 4px;
}

/* ...at the .outcome-word block: */
.outcome-word.published,
.outcome-word.partial {
  color: var(--color-verified);
}

.outcome-word.withheld {
  color: var(--color-critical);
}
```

#### W5-C4 · `web/src/components/MetricTable.tsx` — **MERGED FINAL FILE**

> **This supersedes W3-C10.** It keeps W3's `summary` prop and four states, deletes `showComputedMark` (declaration, default, and use site), and states the fixed six in the screen-reader caption. `FilingPage.tsx` is the only caller that passed the prop; `BriefPage.tsx` and `CompanyPage.tsx` already omit it.

**New code:**
```tsx
import type { MetricRow, MetricState } from "../types";

const STATE_TEXT: Record<MetricState, string> = {
  computed: "Computed",
  unavailable: "Unavailable",
  not_applicable: "Not applicable",
  withheld: "Withheld",
};

const STATE_CLASS: Record<MetricState, string> = {
  computed: "computed",
  unavailable: "missing",
  not_applicable: "missing",
  withheld: "withheld",
};

export function MetricTable({ rows, summary }: { rows: MetricRow[]; summary?: string }) {
  return <div className="metric-block">
    {summary && <p className="metric-summary">{summary}</p>}
    <div className="table-scroll"><table className="metric-table">
      <caption className="sr-only">Deterministic SEC XBRL metric results — the same six starter metrics for every issuer</caption>
      <thead><tr><th>Metric</th><th>Value</th><th>Method &amp; source</th><th>Status</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.source_computation_id}>
        <td>{row.metric}</td><td>{row.value}</td><td><code className="formula">{row.formula}</code><span className="metric-source">computation #{row.source_computation_id} · computed as of {row.effective_as_of}</span></td>
        <td><span className={`trust ${STATE_CLASS[row.state]}`} title={row.state_label} aria-label={row.state_label}>{row.state === "computed" ? "✓ " : ""}{STATE_TEXT[row.state]}</span></td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}
```

> **W6-C7 follow-up:** `STATE_CLASS` maps `unavailable`/`not_applicable` to `"missing"`, which has no CSS rule (`.trust.missing` was dropped by commit 4ee94f0). W6-C7 changes both entries to `""` and the template to `` `trust${STATE_CLASS[row.state] ? " " + STATE_CLASS[row.state] : ""}` ``. The `.trust.withheld` rule added by W3-C14 stays.

#### W5-C5 · `web/src/components/ProvenancePanel.tsx` — drop code labels

**Anchor:** `export const DROP_CODE_LABEL` (5-21); insert after the `AMBIGUOUS_QUOTE` entry.

**Change.** `DUPLICATE_EVIDENCE` (`verify/compiler.py:201-202`) and `EMPTY_HEADLINE` (`verify/compiler.py:101`) are live drop codes with no label, so they render as a bare `<abbr>` with `title={undefined}` (`ProvenancePanel.tsx:91`). The map then has 17 entries, matching the full emitted set derived by W5-T5.

**New code:**
```tsx
export const DROP_CODE_LABEL: Record<string, string> = {
  QUOTE_NOT_EXACT: "The quotation did not match the filing exactly.",
  AMBIGUOUS_QUOTE: "The quotation appeared more than once in the section.",
  DUPLICATE_EVIDENCE: "Another finding already cited exactly this evidence.",
  EMPTY_HEADLINE: "The finding had no headline text.",
  NOT_A_CHANGED_SPAN: "The evidence was not in a changed passage.",
  AUTHORED_NUMBER: "The model-authored headline contained a number.",
  UNSAFE_LANGUAGE: "The headline contained advice or forbidden wording.",
```

Leave the remaining 13 entries exactly as they are.

#### W5-C6 · `web/src/components/FindingList.tsx` — citation label

**Anchor:** the citation anchor inside `citation-line` (line 20).

**Change.** Relabel **only**. `presentation/canonical.py` builds the citation URL from trusted filing identity (`…/Archives/edgar/data/{cik}/{compact}/{accession}-index.htm`), never from a stored URL — that is the right security call and is NOT changed. The link opens the EDGAR filing-index page, so "Open exact SEC source" overclaims. The exact-span proof stays in the adjacent `section_key · chars A–B · <sha256>` metadata span. **Do not deep-link** — that would require `canonical.py` to trust a model- or EDGAR-supplied document path.

**New code:**
```tsx
{citationUrl ? <a className="citation" href={citationUrl} target="_blank" rel="noopener noreferrer">View filing on EDGAR ↗</a> : <span className="citation faint">SEC citation unavailable</span>}
```

#### W5-C7 · `web/src/components/JobProgress.tsx` — typed state gloss

**Change.** Job state renders as a raw lowercase enum inside an aria-live region, and `partial` is unglossed. Add a typed gloss map keyed by `Job["state"]` so adding a state to `types.ts` fails typecheck instead of leaking an enum. Only the `<small>` binding changes; the per-item `{item.message || item.state}` is out of scope (allowlisted server-side in `_safe_item`).

**New code (whole file):**
```tsx
import type { Job } from "../types";

const JOB_STATE_LABEL: Record<Job["state"], string> = {
  queued: "Queued",
  running: "Running…",
  completed: "Completed",
  partial: "Finished — some items did not complete",
  failed: "Failed",
};

export function JobProgress({ job }: { job: Job | null }) {
  if (!job) return null;
  const active = ["queued", "running"].includes(job.state);
  return <div className="job-list" aria-live="polite"><div className="job-heading"><span className={`job-spinner${active ? " active" : ""}`} aria-hidden="true" /><div><strong>{job.kind === "analysis" ? "Filing analysis" : "SEC sync"}</strong><small>{JOB_STATE_LABEL[job.state]}</small></div></div>{job.items.map(item => <div key={item.key} className={`job-item ${item.state}`}><span>{item.stage ?? item.key}</span><strong>{item.message || item.state}</strong></div>)}{job.error && <div className="notice">{job.error}</div>}</div>;
}
```

#### W5-C8 · `web/src/pages/BriefPage.tsx` — captions and follow-up empty state

> **Anchor shift:** after W3-C11 the section kickers are `01 · Filing changes`, `02 · Reviewed`, `03 · SEC XBRL`, `04 · Follow-up`. Apply these edits to the **renumbered** sections.

**Change.** State the at-most-three cap (`grep -rn "three" web/src` currently returns nothing, so three findings read as a complete enumeration) and the fixed six. The follow-up section claims a review happened before anything was reviewed — branch on `brief.tracked_but_unanalyzed`, exactly as the section 01 empty state already does. No new DTO field, no `digest/render.py` change.

**New code:**
```tsx
// 01 · Filing changes caption:
<p className="metric-caption">The model selects significance and publishes at most three findings per filing; RipplX independently checks that every displayed quotation matches the SEC filing exactly.</p>

// 03 · SEC XBRL caption:
<p className="metric-caption">The same six metrics are computed for every issuer by versioned formulas, directly from SEC XBRL facts—never from the language model.</p>

// 04 · Follow-up empty state:
<div className="empty-state compact"><span aria-hidden="true">—</span><div><strong>No open questions</strong><p>{brief.tracked_but_unanalyzed ? "No filing has been reviewed yet, so there is nothing to follow up on." : "Nothing in this brief needs a follow-up review."}</p></div></div>
```

#### W5-C9 · `src/finwatch/presentation/formatting.py` — share count + compressed parts

**Anchor:** the `if metric == "share_count_change":` branch of `format_metric_value` (49-76); the new helper appended after `format_metric_value` ends (line 87, after W1-C5's `plural_count`).

**Current:**
```python
        proven = {
            "up": "share count increased",
            "down": "share count decreased",
            "flat": "share count flat",
        }.get(result.deterministic_direction or "")
        if proven is None:
            material_change = result.value if result.value is not None else 0.0
            proven = (
                "share count decreased"
                if material_change <= -0.0005
                else "share count increased"
                if material_change >= 0.0005
                else "share count flat"
            )
        return f"{_pct(result.value)} YoY ({proven})"
```

**Change (a).** The ±0.0005 fallback is the only live path — SEC companyfacts ships no `decimals`, so `deterministic_direction` (`envelope.py:73-82`, needs both `direction_delta` and `direction_slack`) is None for every real filing — and it emits the **same certified sentence** as a proved direction, while `verify/compiler.py:111-119` drops any model finding asserting that direction as `METRIC_DIRECTION_UNAVAILABLE`. Print the signed change and say the direction is not certified. Nothing is lost. Both surfaces render `MetricRowView.value`, so `digest/render.py` inherits the fix with no change there.

**Change (b).** Add `compressed_metric_parts` for W5-C11 (it needs the private `_pct`/`_num` helpers in scope) and add `from collections.abc import Mapping` (stdlib; no new dependency).

Replace the whole branch including its existing 12-line comment block (50-61).

**New code:**
```python
# at the top of the file, after `from __future__ import annotations`:
from collections.abc import Mapping

from finwatch.metrics.envelope import MetricResult


# ...replacing the whole share_count_change branch:
    if metric == "share_count_change":
        # The publication gate (verify/compiler.py) drops any model finding asserting a
        # direction this metric cannot prove. The table must not assert one either, and
        # must not reuse the certified wording for an uncertified verdict: the SEC
        # companyfacts API ships no `decimals` (zero occurrences across every cached
        # issuer payload and recorded fixture), so `deterministic_direction` is None on
        # the live path. There the signed change is stated and the direction is
        # explicitly uncertified rather than decided by a second display-only heuristic.
        proven = {
            "up": "share count increased",
            "down": "share count decreased",
            "flat": "share count flat",
        }.get(result.deterministic_direction or "")
        if proven is None:
            return (
                f"{_pct(result.value)} YoY "
                "(direction not certified within SEC rounding slack)"
            )
        return f"{_pct(result.value)} YoY ({proven})"


# ...appended after format_metric_value:
def compressed_metric_parts(results: Mapping[str, MetricResult]) -> list[str]:
    """One-line company-row fragments read from validated metric components.

    Never re-parse a formatted display string: ``simple_leverage`` omits the net-debt
    proxy clause entirely when the proxy is undefined, so stripping that prefix from the
    rendered value silently relabels the interest-coverage clause as leverage.
    """
    def _number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    parts: list[str] = []
    revenue = results.get("revenue_growth")
    if revenue is not None and revenue.computed:
        yoy = _number(revenue.components.get("yoy"))
        if yoy is not None:
            parts.append(f"Rev {_pct(yoy)}")
    leverage = results.get("simple_leverage")
    if leverage is not None and leverage.computed:
        ratio = _number(leverage.components.get("net_debt_to_ebitda"))
        if ratio is not None:
            parts.append(f"Leverage proxy {_num(ratio)}×")
    return parts
```

#### W5-C10 · `src/finwatch/presentation/service.py` — **MERGED INTO W3-C3**

No separate work. W3-C3 already produced `_validated_metrics(computations) -> dict[str, tuple[Computation, MetricResult | None]]` and `_metric_rows(validated)`. Confirm with `grep -rn "_metric_rows\|_validated_metrics" src/ tests/` that there is no third caller before proceeding to W5-C11.

#### W5-C11 · `src/finwatch/presentation/service.py` — `companies()` **MERGED FINAL BODY**

> **This supersedes W1-C7's construction and W3-C6.** It carries W1's supported-form filter and renamed field, W3's `STARTER_METRICS` denominator and build-whenever-rows-exist rule, and W5's components-based fragments.

**Change.** When `net_debt_to_ebitda` is None, `format_metric_value` (formatting.py:77-86) emits only the interest-coverage clause, so the old `removeprefix` is a no-op and the row prints "Leverage proxy interest coverage -3.20×" — interest coverage mislabelled as leverage. Read validated `MetricResult.components` instead. Add `compressed_metric_parts` to the line-14 formatting import. `STARTER_METRIC_LABELS` stays imported — `_metric_rows` still uses it.

**New code (whole method):**
```python
    def companies(self) -> CompaniesView:
        result = []
        for company in self.repo.list_tracked_companies(self.user_id):
            # The newest filing of *any* form is not what this row means: ingest also
            # stores Form 4, S-8, DEF 14A and 20-F, none of which this product reads.
            supported = [
                filing
                for filing in self.repo.list_filings(company.cik)
                if base_form(filing.form_type) in ANALYZABLE_FORMS
            ]
            latest = supported[0] if supported else None
            validated = self._validated_metrics(
                self.repo.latest_computations(company.ticker)
            )
            rows = self._metric_rows(validated)
            computed = [row for row in rows if row.state == "computed"]
            compressed = None
            if rows:
                # Read the validated envelopes, never the rendered display string: the
                # leverage line drops its net-debt clause entirely when the proxy is
                # undefined, so prefix-stripping relabels interest coverage as leverage.
                parts = compressed_metric_parts(
                    {
                        name: metric
                        for name, (_row, metric) in validated.items()
                        if metric is not None
                    }
                )
                # The denominator is the fixed starter catalog, never the surviving row
                # count: a dropped or withheld row must not make coverage look complete.
                parts.append(f"✓{len(computed)}/{len(STARTER_METRICS)}")
                compressed = " · ".join(parts)
            result.append(
                CompanyRowView(
                    ticker=company.ticker,
                    cik=company.cik,
                    newest_supported_filing=_date(latest.filed_at) if latest else None,
                    compressed_verified_read=compressed,
                )
            )
        return CompaniesView(companies=sorted(result, key=lambda row: row.ticker))
```

#### W5-C12 · `src/finwatch/presentation/models.py` — `FilingDetailView.sample_data`

**Change.** Mirror `BriefView.sample_data` with the same name, type, and default, so the two surfaces keep one name for one concept. The `False` default means a caller that forgets to pass it can never falsely label real analysis as a sample. Pure additive DTO field.

**New code:**
```python
class FilingDetailView(BaseModel):
    filing: FilingDigestEntry
    verified_numbers: IssuerMetricsView | None = None
    verification: VerificationView | None = None
    withheld_reason: str | None = None
    pipeline: list[PipelineStageView] = Field(default_factory=list)
    research: ResearchTraceView | None = None
    certificate_url: str | None = None
    disclaimer: str = DISCLAIMER
    sample_data: bool = False
```

#### W5-C13 · `src/finwatch/web/app.py` + `src/finwatch/presentation/service.py`

**Change.** Pass the `demo` flag through, exactly as `/api/brief` does. **Land both files together or the route call raises TypeError.** Depends on W5-C12. The two existing non-web callers (`tests/test_presentation_fail_closed.py:98`, `tests/test_pipeline.py:81`) keep working via the default.

> After W4-C7 the `demo = demo and not remote` line is gone and the construction uses `sample_scope`. The merged route body is shown below.

**New code:**
```python
# app.py, filing_detail route body:
        principal = principal_for(request)
        with repo_context(demo) as repo:
            result = PresentationService(
                repo, user_id=sample_scope(principal, demo)
            ).filing(accession, sample_data=demo)
            if result is None:
                raise ApiProblem(404, "filing_not_found", "Filing not found.")
            return result

# service.py, line 205:
    def filing(
        self, accession: str, *, sample_data: bool = False
    ) -> FilingDetailView | None:

# service.py, the FilingDetailView(...) return: add the final keyword after
# certificate_url=..., leaving every other argument unchanged:
            certificate_url=(
                f"/api/filings/{accession}/certificate"
                if research and filing.status in {"verified", "analyzed"}
                else None
            ),
            sample_data=sample_data,
        )
```

> **If Decision D2 is "no"** (W4-C7 not shipped), keep the `demo = demo and not remote` line above `with repo_context(demo)` and pass `user_id=principal.user_id`; `sample_data=demo` is then always `False` in remote mode, which is the correct behavior either way.

#### W5-C14 · `web/src/types.ts`

**New code:**
```typescript
export interface FilingDetail { filing: FilingDigestEntry; verified_numbers: IssuerMetrics | null; verification: Verification | null; withheld_reason: string | null; pipeline: PipelineStage[]; research: ResearchTrace | null; certificate_url: string | null; disclaimer: string; sample_data: boolean }
```

### 6.4 W5 tests

| ID | File | Test | Asserts | Fails before |
|---|---|---|---|---|
| W5-T1 | `web/src/pages/FilingPage.test.tsx` | `renders a partial publication as a verified per-finding outcome, not an alarm` | `const value = detail("partial"); value.research!.dropped_findings = [{ finding_id: "f2", error_codes: ["DUPLICATE_EVIDENCE"] }];` then `renderDetail(value)`. Asserts `findByText("1 finding published, 1 finding removed by the evidence gate")`; `getByLabelText("Publication outcome")` has class `partial`; its `.outcome-glyph` textContent is `"✓"`. Plus direct helper assertions: `outcomeHeadline("partial", 2, 1) === "2 findings published, 1 finding removed by the evidence gate"` and `outcomeHeadline("partial", 1, 0) === researchOutcomeLabel("partial")`. Add `outcomeHeadline` to the import on line 5. **The existing `it.each` block (83-91) needs NO change.** | ✅ |
| W5-T2 | `web/src/pages/FilingPage.test.tsx` | `keeps the deterministic computed mark on XBRL rows when the analysis is withheld` | Render `detail("withheld", { withheld_reason: "…", verified_numbers: { ticker: "TEST", empty: null, summary: "1 computed of 6 starter metrics", rows: [one computed row] } })`. Asserts `findByLabelText("Computed from SEC XBRL facts")` has textContent `"✓ Computed"`. | ✅ |
| W5-T3 | `web/src/pages/FilingPage.test.tsx` | `labels a demo filing page as sample data only when the server says so` | `detail("published", { sample_data: true })` → `findByText(/Sample data · generated by the bundled recorded pipeline/)`. `cleanup()`, then `sample_data: false` → `queryByText(/Sample data/)` null. **`detail()` must gain `sample_data: false` in its base literal** so the fixture typechecks. | ✅ |
| W5-T4 | `web/src/components/ProvenancePanel.test.tsx` | `explains duplicate-evidence and empty-headline drops in plain language` | Render with `dropped_findings: [{ finding_id: "f1", error_codes: ["DUPLICATE_EVIDENCE", "EMPTY_HEADLINE"] }]`. Asserts `getByText("DUPLICATE_EVIDENCE")` has `title` "Another finding already cited exactly this evidence." and `getByText("EMPTY_HEADLINE")` has `title` "The finding had no headline text." | ✅ |
| **W5-T5** | `tests/test_drop_code_labels.py` | `test_every_drop_code_has_a_user_facing_label` | **New file, stdlib only** (`ast`, `re`, `pathlib`, `typing.get_args`). Emitted set: walk the AST of `verify/compiler.py`, collect every `ast.Constant` string passed as `code=` to a `Call` whose func is `Name(id="CompilerIssue")`, plus every `ast.Constant` string argument to a `Call` matching `run_errors.append(...)`; union with `set(get_args(finwatch.llm.harness._SKEPTIC_CODES))`. (Complete: `CompilerIssue` is constructed outside `compiler.py` only at `harness.py:745`, as `CompilerIssue(code=row.code, ...)` where `row.code` is typed `_SKEPTIC_CODES`.) Labelled set: read `ProvenancePanel.tsx`, apply `re.findall(r"^\s*([A-Z][A-Z0-9_]*):", block, re.M)` to the text between `export const DROP_CODE_LABEL` and the first `};`. Assert `len(emitted) == 17`, `emitted <= labelled`, `labelled <= emitted`. | ✅ |
| W5-T6 | `tests/test_presentation.py` | `test_share_count_display_follows_the_deterministic_direction` | **Update the existing test (89-140).** Four certified cases keep their exact strings. Replace the two fallback assertions (137-140) with `format_metric_value(base) == "0.0% YoY (direction not certified within SEC rounding slack)"` and `format_metric_value(base.model_copy(update={"value": -0.017})) == "-1.7% YoY (direction not certified within SEC rounding slack)"`. Add `assert not any(word in format_metric_value(base) for word in ("increased","decreased","flat"))` with a comment that the compiler drops a model finding asserting that direction. Keep the buyback/dilution assertion at 124. Rewrite the docstring (90-98). | ✅ |
| W5-T7 | `tests/test_presentation.py` | `test_company_row_reads_leverage_from_components_not_display_text` | New test on the shared `repo` fixture. Track `TSTC`. Insert two Computations: (a) `revenue_growth`, `computed`, `value=0.175`, `components={"yoy": 0.175}`, `formula_version="revenue_growth.v1"`; (b) `simple_leverage`, `computed`, `value=-3.2`, `components={"interest_coverage": -3.2}` with **no** `net_debt_to_ebitda`, `formula_version="simple_leverage.v2"`. Both need one full `InputUsed` so they survive `_validated_metrics`, and each Computation's `formula_version`/`as_of`/`status` must equal the envelope's. Asserts `"Rev +17.5%" in read`, `"Leverage proxy" not in read`, `"interest coverage" not in read`, `read.endswith("✓2/6")`. | ✅ |
| W5-T8 | `tests/test_web.py` | `test_filing_detail_marks_only_the_demo_dataset_as_sample_data` | Demo-db `TestClient` pattern; accession `0001683168-24-004848`. Asserts `?demo=1` → `sample_data is True`, no flag → `False`. | ✅ |
| W5-T9 | `web/src/components/components.test.tsx` | `labels the evidence citation for the page it actually opens` | Update line 52 from `/SEC source/` to `/View filing on EDGAR/`, same href assertion, plus `expect(screen.queryByText(/exact SEC source/i)).toBeNull()`. | ✅ |
| W5-T10 | `web/src/components/components.test.tsx` | `glosses every job state, including partial` | For each of the five `[state, label]` pairs, render `JobProgress` with an empty-items job, assert `getByText(label)` present and `queryByText(state)` null, then `cleanup()`. Add the `JobProgress` and `cleanup` imports. | ✅ |
| W5-T11 | `web/src/pages/BriefPage.test.tsx` | `states the publication limits and does not claim a review that has not happened` | With `tracked_but_unanalyzed: true`: page contains `/at most three findings per filing/`, `/same six metrics/`, and "No filing has been reviewed yet, so there is nothing to follow up on."; `queryByText("Nothing in this brief needs a follow-up review.")` null. `cleanup()`, re-render with `false`, assert the original sentence returns. | ✅ |

### 6.5 W5 acceptance criteria

1. `uv run pytest -q` green; `cd web && npm test -- --run && npm run typecheck && npm run build` green.
2. A `partial` filing page renders `.outcome-banner.partial` with the ✓ glyph, the verified colour pair, and a headline naming both counts; `metrics_only` falls through to the unqualified amber rule; `withheld` renders `--color-critical` with the 4px rule.
3. `grep -rn "showComputedMark" web/src src/` returns nothing; a withheld filing page and the company page render identical status cells for the same computed metric.
4. Every code the compiler or Skeptic can emit has a `DROP_CODE_LABEL` entry (17 total), and `tests/test_drop_code_labels.py` fails if a code is added without a label, or if a stale label is left behind.
5. `grep -rn "exact SEC source" web/src` returns nothing, and `presentation/canonical.py` is **byte-unchanged** — the citation is still constructed from trusted filing identity.
6. For a `share_count_change` result with no `direction_delta`/`direction_slack`, the rendered value contains none of "increased", "decreased", "flat" — in the browser table **and** in the Markdown digest.
7. `compressed_verified_read` never contains "Leverage proxy interest coverage", is built only from validated `MetricResult.components`, and its ✓ denominator is 6 for every issuer.
8. `/api/filings/{accession}?demo=1` returns `sample_data: true` locally, `false` without the flag; the demo filing page renders the sample-data notice.
9. No new dependency, no new schema object, no change to the publication gate, and no change to which findings or metrics publish. `presentation/canonical.py`, `verify/*`, `metrics/formulas.py`, `metrics/envelope.py`, and `digest/render.py` are untouched by this commit.
10. `diff AGENTS.md CLAUDE.md` empty after the three §8/§11 edits.

### 6.6 W5 doc updates (both mirrors, same commit)

- **§8**, final paragraph. Replace "Presentation language stays narrower than the accounting facts: share-count changes are described only as increased, decreased, or flat—not inferred to be dilution or a buyback." with: *"Presentation language stays narrower than the accounting facts: share-count changes are described only as increased, decreased, or flat—not inferred to be dilution or a buyback—and only when the rounding-aware direction is proved. When `deterministic_direction` is unavailable the row states the signed change and says the direction is not certified within SEC rounding slack, so the table never asserts in certified wording what the compiler would drop as `METRIC_DIRECTION_UNAVAILABLE`."* Keep the surrounding `simple_leverage` and "computed as of" sentences intact and the ~100-column wrapping.
- **§11**, amend the `- exact quotations link to HTTPS SEC pages;` bullet to:
  ```
  - exact quotations are proved by the section key, offsets, and section hash shown beside them, and link to
    the HTTPS SEC filing index page for that accession;
  ```
- **§11**, insert a new bullet after the "verified numbers show state, formula version…" bullet:
  ```
  - the at-most-three finding cap and the fixed six-metric catalog are stated on the surfaces that show
    them, and a `partial` publication is presented as the per-finding gate succeeding, never as a failure;
  ```

### 6.7 W5 risks

- The share-count copy change alters a user-visible string that four assertions in `tests/test_presentation.py` pin (119-123, 130, 137, 140). Update them in the same commit; **do not weaken them to substring matches.**
- `components.test.tsx:52` pins the citation link accessible name and must move with W5-C6 in the same commit.
- W5-C13 spans two files. Land them together with W5-C12 or the DTO rejects the keyword / the route raises TypeError.
- W5-C2's demo notice depends on the whole C12→C13→C14 chain. Skipping any one fails `npm run typecheck` on `detail.sample_data`.
- The `detail()` fixture (`FilingPage.test.tsx:27-53`) is a full `FilingDetail` literal; adding a required `sample_data: boolean` makes it a typecheck error until updated. **Update the base literal, not each call site.**

---

## 7. W6 — Accessibility and dead-code hygiene

**Commit:** `chore: fix focus/contrast accessibility and remove orphaned UI scaffolding`
**Depends on:** all prior workstreams. **W6-C9 and W6-C12 blocked by Decision D3.**

### 7.1 Goal

- Every focusable control has a focus indicator clearing WCAG 2.4.11 (3:1) on both cream page surfaces and dark rail/setup surfaces, **including the filing-type radios**, which are currently `opacity: 0` with no `:focus-within` rule anywhere in `global.css`.
- Interactive control boundaries clear SC 1.4.11 via a new `--color-control-border` token at `rgba(20, 35, 30, 0.52)` (worst case 3.27:1 over `--color-panel-deep`), while decorative hairlines keep their existing light values.
- The selected filing option is identified by a real accent border plus wash rather than a 1.026:1 background shift.
- Fifteen orphaned class names are each resolved deliberately — seven restored, eight deleted — plus the checker-invisible `.trust.missing` modifier, and a new `npm run check:classnames` CI step makes that rot fail the build.
- Fifteen serif font-size declarations collapse into five tokenised steps; ten never-referenced tokens are deleted; the posture chain is removed end to end.

### 7.2 Why it matters

A keyboard user who tabs into the "Run filing analysis" drawer cannot see which filing family is focused: the radios are `opacity: 0`, there is no `:focus-within` rule in all 1,613 lines of `global.css`, and the single global focus ring (`rgba(22, 117, 95, 0.24)` at `global.css:42`) measures **1.405:1** against `--color-canvas`. Control boundaries at 1.475:1 mean an empty input has no perceivable edge. Worse, `.filing-option input { position: absolute }` has no positioned ancestor, so inside the analysis Drawer (a modal dialog, itself the containing block) **all four hidden radios stack at the dialog origin** — `opacity: 0` elements remain clickable, so this is a live wrong-option hit-target bug.

### 7.3 Changes

#### W6-C1 · `web/src/styles/tokens.css` (whole file)

**Change.** (a) **DELETE** the ten tokens with zero `var()` references: `--color-faint` (byte-identical duplicate of `--color-muted`), `--color-withheld`, `--text-display`, `--text-lede`, `--tracking-badge`, `--tracking-tight`, `--radius-xl`, `--radius-md`, `--shadow-sm`, `--space-section`. **KEEP** `--color-accent-strong` (also unreferenced today) — W6-C2 makes it the focus-ring colour.
(b) **ADD** `--color-control-border` and `--border-control`. **Do NOT raise `--color-hairline-strong`** — it is also the decorative rule colour for `.company-list` (1061), `.provenance` (846) and the `.metric-table th` group; that restraint is deliberate.
(c) **REPLACE** `--text-h1`/`--text-h2` with a five-step serif scale; W6-C5 rewires every call site.

> **Conflict with W3-C14:** W3 uses `--color-withheld` for `.trust.withheld`. **Keep `--color-withheld` in the token file** — it is referenced after W3, so the "every token is referenced" test (W6-T4) passes with it present. Delete only the other nine.
> **Conflict with W4-C13:** `.onboarding-copy h2` uses `--text-h2` and `.specimen-tag` uses `--tracking-badge`. Rewire both in W6-C5 (see the note in W4-C13).

Measured contrast for `rgba(20,35,30,0.52)` composited over each launch surface and compared to that surface: `#ffffff` 3.42, `#fafbf7` 3.38, `#f7f8f4` 3.36, `#f2f4ef` 3.32, `#eaf6f0` 3.32, `#f8eee4` 3.30, `#e9eee7` 3.27 — all clear SC 1.4.11.

**New code:**
```css
:root {
  --color-bg: #f2f4ef;
  --color-canvas: #fafbf7;
  --color-panel: #ffffff;
  --color-panel-alt: #f7f8f4;
  --color-panel-deep: #e9eee7;
  --color-ink: #14231e;
  --color-body: #3e4d47;
  --color-muted: #66736e;
  --color-faint-2: #74817b;
  /* Decorative rules stay deliberately faint; interactive edges must clear 3:1. */
  --color-hairline: rgba(20, 35, 30, 0.11);
  --color-hairline-strong: rgba(20, 35, 30, 0.19);
  --color-control-border: rgba(20, 35, 30, 0.52);
  --color-accent: #16755f;
  --color-accent-strong: #0f5747;
  --color-accent-soft: #ccebdd;
  --color-accent-wash: #eaf6f0;
  --color-sidebar: #102a24;
  --color-sidebar-soft: #17372f;
  --color-sidebar-text: #edf4f0;
  --color-warn: #a65e24;
  --color-warn-wash: #f8eee4;
  --color-critical: #9c4437;
  --color-withheld: #8a6d3b;
  --color-verified: var(--color-accent);

  --font-serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
  --font-sans: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;

  /* Five serif steps. Every serif size in the app is one of these. */
  --text-serif-xs: 18px;
  --text-serif-sm: 21px;
  --text-serif-md: 25px;
  --text-serif-lg: clamp(22px, 2.9vw, 31px);
  --text-serif-xl: clamp(29px, 4.6vw, 50px);

  --text-body-lg: 17px;
  --text-body: 15px;
  --text-body-sm: 14px;
  --text-kicker: 11px;
  --text-caption: 12px;
  --tracking-kicker: 0.14em;

  --radius-pill: 999px;
  --radius-lg: 20px;
  --radius-sm: 10px;
  --border-hair: 1px solid var(--color-hairline);
  --border-strong: 1px solid var(--color-hairline-strong);
  --border-control: 1px solid var(--color-control-border);
  --shadow-lg: 0 24px 70px rgba(16, 42, 36, 0.13);
  --space-page-x: clamp(24px, 5vw, 72px);
  --space-page-y: clamp(30px, 5vw, 62px);
}
```

> Keep `--color-withheld`'s existing value from the current `tokens.css:25` rather than the placeholder above if it differs; the point is that the token survives.

#### W6-C2 · `web/src/styles/global.css` — focus indicator

**Anchor:** the only `outline` declaration in `web/src` (37-44).

**Current:**
```css
button:focus-visible,
a:focus-visible,
input:focus-visible,
select:focus-visible,
summary:focus-visible {
  outline: 3px solid rgba(22, 117, 95, 0.24);
  outline-offset: 3px;
}
```

**Change.** The outer 2px `#0f5747` outline measures 8.17:1 on `--color-canvas`, 8.49:1 on `--color-panel`, 7.66:1 on `--color-bg`, worst cream surface 7.21:1 on `--color-panel-deep`. It measures only 1.79:1 against the dark rail `#102a24`, where `.nav-link`, the sign-in form and the setup form all live, so **the white box-shadow ring filling the 2px offset gap is load-bearing, not decorative**: `#ffffff` on `#102a24` is 15.23:1. Add `.filing-option:focus-within` so the visually hidden radio lights up its whole row. `global.css` contains exactly one `box-shadow` today (`.drawer` at 1329), so no control loses an existing shadow. Neither declaration is animated, so the `prefers-reduced-motion` block (1606-1613) continues to hold.

**New code:**
```css
button:focus-visible,
a:focus-visible,
input:focus-visible,
select:focus-visible,
summary:focus-visible,
.filing-option:focus-within {
  /* 7.2:1 or better on the cream surfaces; the white ring carries the dark rail at 15.2:1. */
  outline: 2px solid var(--color-accent-strong);
  outline-offset: 2px;
  box-shadow: 0 0 0 2px var(--color-panel);
}
```

#### W6-C3 · `web/src/styles/global.css` — control borders + restored input variants

**Anchor:** the `.button, .sec-link, .text-link, .certificate-download, .proof-toggle {` group (277-296) and `.input {` (1128-1135) with `.input:focus {` (1138-1140).

**Change.** Swap `border: var(--border-strong)` for `border: var(--border-control)` in both. `.button.primary` (307-311) and `.button.ghost` (317-321) override border-color; `.sec-link, .text-link` set `border: 0` at 765-773 — all unaffected. Then restore three `.input` variants. `.input.mono-input` is emitted seven times and has had no rule since 4ee94f0; the 4ee94f0^ rule also set `font-size: 12px`, which would shrink seven fields including the API-key and sign-in-code inputs — **restore the font-family only**. Because `.input.mono-input` no longer sets a size, the `!important` that the old `.ticker-input`/`.otp-input` rules needed is unnecessary — use equal-specificity `.input.ticker-input` / `.input.otp-input` declared after it.

**New code:**
```css
.button,
.sec-link,
.text-link,
.certificate-download,
.proof-toggle {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  padding: 9px 15px;
  color: var(--color-ink);
  background: transparent;
  border: var(--border-control);
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-weight: 650;
  text-decoration: none;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

/* ...unchanged rules between the two blocks... */

.input {
  width: 100%;
  min-height: 46px;
  padding: 10px 12px;
  color: var(--color-ink);
  background: var(--color-panel);
  border: var(--border-control);
  border-radius: var(--radius-sm);
}

.input:focus {
  border-color: var(--color-body);
}

.input.mono-input {
  font-family: var(--font-mono);
}

.input.ticker-input {
  height: 60px;
  font-size: 20px;
  text-transform: uppercase;
}

.input.otp-input {
  font-size: 22px;
  letter-spacing: 0.32em;
  text-align: center;
}
```

#### W6-C4 · `web/src/styles/global.css` — filing-option

**Anchor:** the three consecutive rules `.filing-option {`, `.filing-option.selected {`, `.filing-option input {` (1253-1272).

**Change.** Four fixes. (1) `position: relative` — the hidden radios are `position: absolute` with no positioned ancestor, so in the Drawer all four stack at the dialog's top-left corner; this is the overlapping click-target bug **and** the reason `:focus-within` has nothing to anchor to. (2) Explicit 1px dimensions and zero margin so the absolute box stays inside its row. **Keep it in the layout — never `display: none` or `visibility: hidden`** — so it remains focusable. (3) Raise the unselected border from `--border-hair` (1.243:1) to `--border-control`. (4) Replace the 1.026:1 selected state with the accent border (`#16755f` is 5.06:1 against the wash, 5.40:1 against the canvas) plus the wash, and tint the existing aria-hidden check glyph. **Selection is not conveyed by colour alone in either state**: the radio's own checked state is what assistive technology reads, and `FilingTypePicker.tsx:19` already renders a visible check character for the selected option.

**New code:**
```css
.filing-option {
  position: relative;
  display: grid;
  grid-template-columns: 46px 1fr 24px;
  gap: 12px;
  align-items: center;
  padding: 13px;
  border: var(--border-control);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.filing-option.selected {
  border-color: var(--color-accent);
  background: var(--color-accent-wash);
}

.filing-option.selected .filing-option-check {
  color: var(--color-accent);
}

/* Hidden but focusable, and anchored to its own row so :focus-within can show it. */
.filing-option input {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: 0;
  opacity: 0;
}
```

#### W6-C5 · `web/src/styles/global.css` — serif scale

**Anchor:** fifteen serif `font-size` declarations, currently at lines 68, 73, 123, 256, 408, 441, 478, 563, 595, 622, 684, 721, 1091, 1379, 1461. **Locate each by its selector, not its line number.**

**Mapping, in file order:**

| Selector | Current | New token |
|---|---|---|
| `h2` | `var(--text-h2)` | `--text-serif-lg` |
| `h3` | `20px` | `--text-serif-sm` |
| `.brand-copy strong` | `26px` | `--text-serif-md` |
| `.page-title` | `var(--text-h1)` | `--text-serif-xl` |
| `.answer-hero` | `clamp(28px, 4vw, 46px)` | `--text-serif-xl` |
| `.brief-stats strong` | `22px` | `--text-serif-sm` |
| `.empty-invitation h2` | `clamp(30px, 5vw, 48px)` | `--text-serif-xl` |
| `.filing-link` | `24px` | `--text-serif-md` |
| `.finding-heading h3` | `clamp(22px, 3vw, 30px)` | `--text-serif-lg` |
| `.quote` | `clamp(21px, 3vw, 30px)` | `--text-serif-lg` |
| `.metric-table td:nth-child(2)` | `18px` | `--text-serif-xs` |
| `.issuer-title` | `25px` | `--text-serif-md` |
| `.holding-title strong` | `27px` | `--text-serif-md` |
| `.setup-card h1` | `clamp(32px, 5vw, 52px)` | `--text-serif-xl` |
| `@840px .brand-copy strong` | `21px` | `--text-serif-sm` |

**Plus, from W4-C13:** `.onboarding-copy h2` → `--text-serif-lg`; `.specimen .quote` → `--text-serif-md`; `.specimen-tag { letter-spacing: var(--tracking-badge) }` → `var(--tracking-kicker)`.
**Plus, from W3-C14:** `.brief-stats strong.window-label` keeps its explicit `13px` — it is mono, not serif.

**Do NOT touch** `.drawer-close { font-size: 24px; }` (1354) — sans. **Do NOT touch** `.proof-block h3 { font-family: var(--font-sans); font-size: 15px; }` (995-998) — explicitly opts out.

After this edit `font-size: clamp(` must not appear anywhere in `global.css`; W6-T4 pins that. The xl minimum is 29px, inside the 28-34px range of the four minima it replaces, so the 640px reflow does not regress.

**New code (representative):**
```css
h2 {
  margin-bottom: 10px;
  font-size: var(--text-serif-lg);
}

h3 {
  margin-bottom: 8px;
  font-size: var(--text-serif-sm);
}

/* ... */

.page-title {
  margin-bottom: 10px;
  font-size: var(--text-serif-xl);
}

/* ... */

.answer-hero {
  margin: 10px 0 16px;
  font-family: var(--font-serif);
  font-size: var(--text-serif-xl);
  line-height: 1.15;
  letter-spacing: -0.035em;
}

/* ... */

.quote {
  margin: 0;
  padding: 4px 0 6px 28px;
  color: var(--color-ink);
  border-left: 4px solid var(--color-accent-soft);
  font-family: var(--font-serif);
  font-size: var(--text-serif-lg);
  line-height: 1.55;
}

/* ... */

.metric-table td:nth-child(2) {
  font-family: var(--font-serif);
  font-size: var(--text-serif-xs);
  font-weight: 600;
  white-space: nowrap;
}
```

#### W6-C6 · `web/src/styles/global.css` — restore four orphaned rules

**Anchor:** place each adjacent to its related selector — `.date-control` after `.section-header {` (360-363); `.holding-copy` after `.holding-row-link {` (1073-1081); `.compact-field` after `.field {` (1116-1119); `.picker-helper` after `.helper {` (1142-1145). Also extend the `@media (max-width: 640px)` block (from 1496).

**Change.** Restore the four orphans that carry real layout value, adapted from `git show 4ee94f0^:web/src/styles/global.css` (lines 170, 193, 194-195, 225). `.date-control .input { width: auto; }` is the important one: without it the `type=date` field on CompanyPage inherits `.input { width: 100% }` and pushes its own label off the row. Additionally, 4ee94f0^ carried `.date-control` in its 640px reflow lists (337-338) and the current file does not; add it to both mobile groups so the date row cannot overflow at 375px.

**New code:**
```css
/* place immediately after .section-header */
.date-control {
  display: flex;
  gap: 10px;
  align-items: center;
}

.date-control .input {
  width: auto;
}

/* place immediately after .holding-row-link */
.holding-copy {
  min-width: 0;
}

/* place immediately after .field */
.compact-field {
  max-width: 300px;
}

/* place immediately after .helper */
.picker-helper {
  margin: 5px 0 14px;
}

/* inside @media (max-width: 640px): add .date-control to the stacking group.
   NOTE: `.guidance-note` is removed from this list by W4-C12. */
  .page-header,
  .brief-header,
  .filing-detail-hero,
  .reading-heading,
  .proof-heading,
  .surface-header,
  .settings-actions,
  .analysis-submit,
  .date-control {
    align-items: flex-start;
    flex-direction: column;
  }

/* inside @media (max-width: 640px): add .date-control .input to the full-width group */
  .actions,
  .stacked-mobile,
  .date-control .input {
    width: 100%;
  }
```

#### W6-C7 · Three orphaned class names in components

**Anchor:** `MetricTable.tsx` trust cell (as rewritten by W5-C4); `AnalysisPanel.tsx:32-35`; `ProvenancePanel.tsx:84`.

**Change.**
1. **`.trust.missing`** was dropped by 4ee94f0; its 4ee94f0^ definition (line 150) was `background: var(--color-panel-deep); color: var(--color-faint-2)` — a chip treatment the current restrained design deliberately abandoned. Emit no modifier for non-computed states rather than reinstating the chip. **This orphan is invisible to the W6-C10 checker** (it lives inside a template-literal hole), so W6-T5 pins it instead.
2. **`.status-dot`** was a decorative green dot inside a tinted `.analysis-intro` card; 4ee94f0 removed both the card background and the dot rule, so the span renders as a zero-size nothing that only contributes a stray 14px flex gap. **Delete the element**, not just the class — it is aria-hidden and carries no meaning.
3. **`.primary-proof-facts`** has never been defined in this file's history. Drop the token, keep `proof-facts`.

**New code:**
```tsx
// MetricTable.tsx — STATE_CLASS and the trust cell (building on W5-C4)
const STATE_CLASS: Record<MetricState, string> = {
  computed: "computed",
  unavailable: "",
  not_applicable: "",
  withheld: "withheld",
};

// ...the trust cell:
        <td><span className={`trust${STATE_CLASS[row.state] ? ` ${STATE_CLASS[row.state]}` : ""}`} title={row.state_label} aria-label={row.state_label}>{row.state === "computed" ? "✓ " : ""}{STATE_TEXT[row.state]}</span></td>
```
```tsx
// AnalysisPanel.tsx — delete the decorative span entirely
  return <div className="analysis-panel">
    <div className="analysis-intro">
      <div><strong>Evidence-first analysis</strong><p>RipplX analyzes only the newest filing in the family you choose — it never falls back to older filings. Exact SEC quotations and deterministic checks gate every published finding.</p></div>
    </div>
```
```tsx
// ProvenancePanel.tsx line 84
      <dl className="proof-facts">
```

#### W6-C8 · Six orphaned class names across five files

**Change.** `company-header`, `date-input`, `watchlist-section` and `filing-page` have never been defined — bare hooks with no rule; delete the tokens, keep the base classes. `.watermark` (both call sites) **was** defined at 4ee94f0^:279, but restoring it also requires re-adding `position: relative; overflow: hidden` to `.setup` (present at 4ee94f0^:278, absent from the current `.setup` at 1362-1368) or the 720px decorative circle escapes its container and creates page-level scroll. 4ee94f0 removed that flourish deliberately — **delete both `<div className="watermark" aria-hidden="true" />` elements, and ONLY that element**, preserving everything else on the line (App.tsx keeps `className="setup-card unlock-card"`). On `FilingPage.tsx` the literal `withheld` modifier on the no-research banner needs no rule — the base `.outcome-banner` (775-784) is already the warn-coloured withheld appearance. Dropping it also lets the checker run with no allowlist.

> **Conflict with W1-C12 and W5-C3:** W1-C12 makes that banner's class expression `` `outcome-banner ${pipelineFailed ? "not-analyzed" : "withheld"}` `` — a template hole, invisible to the checker — and W5-C3 **adds** a real `.outcome-banner.withheld` rule. So after W1 and W5, `withheld` is no longer an orphan. **Skip the `FilingPage.tsx:77` edit in W6-C8**; apply only the five other deletions.

**New code:**
```tsx
// CompanyPage.tsx line 28 — two edits on the same line
  return <main className="page"><button className="button ghost back-button" onClick={() => navigate(-1)}>← Back to companies</button><header className="page-header"><div>…
  …<input id="as-of" className="input mono-input" type="date" value={asOf} onChange={event => setAsOf(event.target.value)} />…

// SetupPage.tsx line 19 — drop only the decorative element
  return <main className="setup"><section className="setup-card"><div className="setup-kicker">RipplX</div>…

// App.tsx line 68 — drop only the decorative element; keep unlock-card
  return <main className="setup"><section className="setup-card unlock-card"><div className="setup-kicker">RipplX public alpha</div>…

// CompaniesPage.tsx line 21
    … : <section className="section"><div className="surface-header">…

// FilingPage.tsx line 66 (already applied by W5-C2)
  return <main className="page">
```

#### W6-C9 · Posture chain — React side — **BLOCKED BY DECISION D3**

**Anchor:** `BriefPage.tsx:32` (`const answerTone = …`) and line 37; `web/src/components/PosturePill.tsx` (whole file); `components.test.tsx` lines 8 and 20-23; `types.ts` lines 1 and 11.

**Change.**
1. Delete `answerTone` and its interpolation. `.brief-hero.calm` and `.brief-hero.attention` were gradient backgrounds on the old dark hero card (4ee94f0^:69-70); 4ee94f0 replaced the hero with the current hairline-bounded neutral block (392) and removed both rules, so the variable has produced a no-op class for the whole life of the current design. Restoring a severity-derived colour wash on the executive answer would re-introduce an affective posture signal into a surface §1 requires to stay free of P3 posture — **delete rather than restore.**
2. Delete `PosturePill.tsx`, its import at `components.test.tsx:8`, and the test at 20-23. The component renders the raw token `critical_review` into the UI, its only importer is that test, and no page renders it. Its "teal" branch has no `.pill.teal` rule either.
3. Delete the `Posture` type alias and `answer_posture` from `Brief`.

Run `npm run typecheck` after all three: TypeScript is the check that no other consumer exists.

**New code:**
```tsx
// BriefPage.tsx — delete line 32 and the interpolation on 37
  const sample = brief.sample_data;
  const trackedTickers = brief.tracked_tickers;
  return <main className="page">
    …
    <section className="brief-hero">

// delete the component file
// rm web/src/components/PosturePill.tsx

// components.test.tsx — remove the import on line 8 and the block on lines 20-23
// -import { PosturePill } from "./PosturePill";
// -  it("renders posture values without synonyms", () => {
// -    render(<PosturePill posture="critical_review" />);
// -    expect(screen.getByText("critical_review")).toBeInTheDocument();
// -  });

// types.ts — line 1 deleted, Brief loses answer_posture
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type MetricState = "computed" | "unavailable" | "not_applicable" | "withheld";
export type FilingType = "latest" | "10-K" | "10-Q" | "8-K";
```

The final `Brief` interface, with `answer_posture` removed and every prior workstream's field present:
```typescript
export interface Brief { period: { covered_label: string; filings_in_window: number; analyzed_filings: number; published_filings: number; withheld_filings: number; filings_tracked_total: number; outside_window: string | null }; tracked_tickers: string[]; answer: string; filings: FilingDigestEntry[]; gate_removed_filings: FilingDigestEntry[]; verified_numbers: IssuerMetrics[]; open_questions: string[]; reviewed_filings: FilingDigestEntry[]; withheld_filings: FilingDigestEntry[]; tracked_but_unanalyzed: boolean; filings_synced: number; disclaimer: string; sample_data: boolean }
```

#### W6-C10 · `web/scripts/check-class-names.mjs` + package.json + vite.config.ts + ci.yml

**Change.** Three tooling edits, **no new npm dependency.**

(a) A dependency-free Node checker that fails when a `className` string literal in `web/src` has no matching selector in `web/src/styles/*.css`. It reads static `className="…"` values and the literal chunks of `` className={`…`} `` templates, replacing `${…}` holes with whitespace. It deliberately does not resolve conditional expressions; that limit is stated in the file header, not papered over with an allowlist. **No allowlist file is needed** because W6-C6 through W6-C9 resolve every current orphan. `.mjs` is invisible to both tsconfig projects, so `npm run typecheck` is unaffected.

(b) Add `css: true` to the `test` block in `web/vite.config.ts`. **REQUIRED for W6-C11**: vitest stubs CSS modules to an empty string by default, so `import globalCss from "./global.css?raw"` returns `""`. Reading with `node:fs` is not an option — `@types/node` is not a dependency of `web/` and adding one needs explicit permission. Verified locally: with `css: true` all existing frontend tests still pass and typecheck is clean.

**New code:**
```javascript
// web/scripts/check-class-names.mjs
// Fail the build when a className literal in web/src has no matching selector in
// web/src/styles.  Commit 4ee94f0 deleted rules while leaving the class names in
// TSX, which is silent and invisible in review.  Limitation, on purpose: only
// static strings and the literal chunks of template strings are checked;
// `${...}` holes are runtime values and are skipped.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src/", import.meta.url));

function walk(directory) {
  return readdirSync(directory).flatMap(entry => {
    const full = join(directory, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const files = walk(root);
const css = files.filter(file => file.endsWith(".css")).map(file => readFileSync(file, "utf8")).join("\n");
const defined = new Set([...css.matchAll(/\.([A-Za-z][\w-]*)/g)].map(match => match[1]));

const CLASS_ATTRIBUTE = /className=(?:"([^"]*)"|\{`([^`]*)`\})/g;
const problems = [];

for (const file of files.filter(name => name.endsWith(".tsx"))) {
  const source = readFileSync(file, "utf8");
  for (const match of source.matchAll(CLASS_ATTRIBUTE)) {
    const literal = (match[1] ?? match[2]).replace(/\$\{[^}]*\}/g, " ");
    const line = source.slice(0, match.index).split("\n").length;
    for (const name of literal.split(/\s+/).filter(Boolean)) {
      if (!defined.has(name)) {
        problems.push(`${file.slice(root.length)}:${line} .${name}`);
      }
    }
  }
}

if (problems.length > 0) {
  console.error(`Undefined CSS class names (${problems.length}):`);
  for (const problem of problems) console.error(`  ${problem}`);
  console.error("Define the rule in web/src/styles or delete the class name.");
  process.exit(1);
}

console.log(`All className literals resolve to a selector (${defined.size} defined).`);
```
```jsonc
/* --- web/package.json --------------------------------------------------- */
  "scripts": {
    "dev": "vite",
    "build": "npm run typecheck && vite build",
    "typecheck": "tsc --noEmit -p tsconfig.app.json --pretty false && tsc --noEmit -p tsconfig.node.json --pretty false",
    "check:classnames": "node scripts/check-class-names.mjs",
    "test": "vitest run"
  },
```
```ts
/* --- web/vite.config.ts: the test block ---------------------------------- */
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "https://alpha.example" } },
    setupFiles: "./src/test/setup.ts",
    // styles.test.ts reads the stylesheets through `?raw`; without this vitest
    // stubs CSS modules to an empty string.
    css: true,
  },
```
```yaml
# --- .github/workflows/ci.yml, after the "Install frontend dependencies" step ---
      - name: Check CSS class names
        working-directory: web
        run: npm run check:classnames
```

#### W6-C11 · `web/src/styles/styles.test.ts` (new file)

**Change.** Contrast and token-hygiene test pinning W6-C1 through W6-C5. Reads both stylesheets through Vite `?raw` (enabled by `css: true`), so no `node:fs` and no new dependency. Two implementation details that matter under this tsconfig: `noUncheckedIndexedAccess` is on, so every regex capture must be narrowed; and `block()` must anchor on a newline (`\n.input {`) because a plain `indexOf(".input {")` would match inside `.date-control .input {`, which W6-C6 places earlier. Use `.proof-toggle` to reach the multi-selector `.button` group.

**New code:**
```ts
import { describe, expect, it } from "vitest";
import globalCss from "./global.css?raw";
import tokensCss from "./tokens.css?raw";

/** Every surface a control border or focus ring can land on in the launch UI. */
const CREAM_SURFACES = [
  "--color-bg",
  "--color-canvas",
  "--color-panel",
  "--color-panel-alt",
  "--color-panel-deep",
  "--color-accent-wash",
  "--color-warn-wash",
];

type Rgba = [number, number, number, number];

function token(name: string): string {
  const value = new RegExp(`${name}:\\s*([^;]+);`).exec(tokensCss)?.[1];
  if (value === undefined) throw new Error(`token ${name} is not declared`);
  return value.trim();
}

function parseColor(value: string): Rgba {
  const hex = /^#([0-9a-fA-F]{6})$/.exec(value)?.[1];
  if (hex !== undefined) {
    return [
      parseInt(hex.slice(0, 2), 16),
      parseInt(hex.slice(2, 4), 16),
      parseInt(hex.slice(4, 6), 16),
      1,
    ];
  }
  const body = /^rgba?\(([^)]+)\)$/.exec(value)?.[1];
  if (body === undefined) throw new Error(`unsupported colour: ${value}`);
  const parts = body.split(",").map(part => Number(part.trim()));
  const [red, green, blue, alpha] = parts;
  if (red === undefined || green === undefined || blue === undefined) {
    throw new Error(`unsupported colour: ${value}`);
  }
  return [red, green, blue, alpha ?? 1];
}

/** Composite a translucent colour over an opaque backdrop. */
function over(top: Rgba, bottom: Rgba): Rgba {
  const alpha = top[3];
  return [
    top[0] * alpha + bottom[0] * (1 - alpha),
    top[1] * alpha + bottom[1] * (1 - alpha),
    top[2] * alpha + bottom[2] * (1 - alpha),
    1,
  ];
}

function luminance([red, green, blue]: Rgba): number {
  const channel = (value: number) => {
    const scaled = value / 255;
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue);
}

function contrast(first: Rgba, second: Rgba): number {
  const left = luminance(first);
  const right = luminance(second);
  return (Math.max(left, right) + 0.05) / (Math.min(left, right) + 0.05);
}

/** The declaration body of a rule whose selector starts its own line. */
function block(selector: string): string {
  const start = globalCss.indexOf(`\n${selector} {`);
  if (start < 0) throw new Error(`selector ${selector} is not defined`);
  const end = globalCss.indexOf("}", start);
  return globalCss.slice(start, end);
}

const declaredTokens = [...tokensCss.matchAll(/^\s*(--[a-z0-9-]+):/gm)]
  .map(match => match[1])
  .filter((name): name is string => name !== undefined);

describe("launch surface accessibility", () => {
  it("gives every focusable control a 3:1 indicator on cream and on the dark rail", () => {
    const focus = block(".filing-option:focus-within");
    expect(focus).toContain("outline: 2px solid var(--color-accent-strong)");
    expect(focus).toContain("box-shadow: 0 0 0 2px var(--color-panel)");
    expect(globalCss).not.toContain("rgba(22, 117, 95, 0.24)");

    const ring = parseColor(token("--color-accent-strong"));
    for (const surface of CREAM_SURFACES) {
      expect(contrast(ring, parseColor(token(surface)))).toBeGreaterThanOrEqual(3);
    }
    const offsetRing = parseColor(token("--color-panel"));
    expect(contrast(offsetRing, parseColor(token("--color-sidebar")))).toBeGreaterThanOrEqual(3);
  });

  it("clears 3:1 on control borders while decorative hairlines stay restrained", () => {
    const control = parseColor(token("--color-control-border"));
    for (const surface of CREAM_SURFACES) {
      const backdrop = parseColor(token(surface));
      expect(contrast(over(control, backdrop), backdrop)).toBeGreaterThanOrEqual(3);
    }

    const panel = parseColor(token("--color-panel"));
    for (const decorative of ["--color-hairline", "--color-hairline-strong"]) {
      const rule = parseColor(token(decorative));
      expect(contrast(over(rule, panel), panel)).toBeLessThan(2);
    }

    expect(block(".proof-toggle")).toContain("border: var(--border-control)");
    expect(block(".input")).toContain("border: var(--border-control)");
    expect(block(".filing-option")).toContain("border: var(--border-control)");
  });

  it("keeps the hidden filing-type radio focusable inside its own row", () => {
    expect(block(".filing-option")).toContain("position: relative");

    const radio = block(".filing-option input");
    expect(radio).toContain("opacity: 0");
    expect(radio).not.toContain("display: none");
    expect(radio).not.toContain("visibility: hidden");

    expect(block(".filing-option.selected")).toContain("border-color: var(--color-accent)");
  });

  it("references every declared token and keeps the serif scale at five steps", () => {
    const stylesheets = `${tokensCss}${globalCss}`;
    for (const name of declaredTokens) {
      expect(stylesheets).toContain(`var(${name})`);
    }

    expect(declaredTokens.filter(name => name.startsWith("--text-serif-"))).toHaveLength(5);
    expect(declaredTokens).not.toContain("--color-faint");
    expect(globalCss).not.toContain("font-size: clamp(");
    expect(block(".answer-hero")).toContain("font-size: var(--text-serif-xl)");
    expect(block(".quote")).toContain("font-size: var(--text-serif-lg)");
  });
});
```

#### W6-C12 · Posture chain — backend — **BLOCKED BY DECISION D3**

**Anchor:** `Posture` (models.py:11-13) and `answer_posture: Posture | None = None` in `BriefView`; the `answer_posture` initialiser and five assignments in `PresentationService.brief` (as written by W3-C5); the `answer_posture=answer_posture,` kwarg; the stale kwarg at `tests/test_digest.py:119`.

**Change.**
1. Delete the `Posture` literal and the `answer_posture` field. `Literal` is still needed for `Severity` and `MetricState` — keep the import.
2. In `service.py`, delete the `answer_posture = None` initialiser, **all five** assignments (W1-C6/W3-C5 added a sixth branch), and the kwarg. The plain-sentence `answer` strings are unchanged — this removes only the parallel machine-readable tone token. The `severe` computation stays: it still selects the "needs a critical review." wording.
3. Delete the stale kwarg at `tests/test_digest.py:119`.

**Verified by grep, not assumed:** `digest/render.py` reads only `brief.tracked_tickers`, `brief.answer`, `brief.period.*`, `brief.withheld_filings`, `brief.gate_removed_filings`, `brief.filings`, `brief.verified_numbers`, `brief.open_questions`, `brief.reviewed_filings`, `brief.tracked_but_unanalyzed`, `brief.filings_synced`, `brief.disclaimer` — **never** `answer_posture` — so the §11 mirror obligation imposes no edit there. **State this explicitly in the commit message.**

After the change, `grep -rn 'posture' src/finwatch tests web/src` must return exactly three hits: `src/finwatch/core/text_policy.py:52`, `:88` (prose comments), and `src/finwatch/demo/data/going_concern.p3.json:4` (`review_posture` in an inert P3 demo fixture — leave it alone).

**New code:**
```python
# src/finwatch/presentation/models.py — top of file
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from finwatch.core.types import DISCLAIMER

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
MetricState = Literal["computed", "unavailable", "not_applicable", "withheld"]
WithheldKind = Literal["gate", "pipeline_failed"]
FilingOutcome = Literal[
    "published",
    "no_findings",
    "findings_dropped",
    "withheld_gate",
    "pipeline_failed",
    "not_analyzed",
]
```
```python
# src/finwatch/presentation/service.py — answer selection (posture lines removed)
        if gate_withheld:
            answer = (
                f"{plural_count(len(gate_withheld), 'filing')} withheld — "
                "could not be verified."
            )
        elif pipeline_failed:
            answer = (
                f"{plural_count(len(pipeline_failed), 'filing')} could not be analyzed — "
                "the pipeline did not complete."
            )
        elif severe:
            answer = "A tracked company needs a critical review."
        elif published:
            answer = f"Important changes found in {plural_count(len(published), 'filing')}."
        elif gate_removed:
            answer = (
                f"Every proposed change in {plural_count(len(gate_removed), 'filing')} "
                "failed the evidence gate. Verified numbers still published."
            )
        elif analyzed:
            answer = (
                "Nothing important changed. "
                f"{plural_count(len(reviewed), 'routine filing')} reviewed."
            )
        elif outside_window:
            answer = "No tracked filing falls inside your reading window."
        elif tracked:
            answer = "No material findings yet — sync filings or run analysis."
        else:
            answer = "Add a ticker to start your brief."
```

The `BriefView(...)` construction loses only the `answer_posture=answer_posture,` line.

### 7.4 W6 tests

| ID | File | Test | Asserts | Fails before |
|---|---|---|---|---|
| W6-T1 | `web/src/styles/styles.test.ts` | `gives every focusable control a 3:1 indicator on cream and on the dark rail` | The focus rule declares the 2px outline and the white ring; the old `rgba(22, 117, 95, 0.24)` is gone; `#0f5747` ≥ 3:1 against all seven cream surfaces (worst 7.21); `--color-panel` ≥ 3:1 against `--color-sidebar` (15.23). Reaching the rule via `block(".filing-option:focus-within")` also proves the selector exists. | ✅ |
| W6-T2 | `web/src/styles/styles.test.ts` | `clears 3:1 on control borders while decorative hairlines stay restrained` | `--color-control-border` composited over each of the seven surfaces ≥ 3 (worst 3.27). `--color-hairline` (1.243) and `--color-hairline-strong` (1.475) over `--color-panel` both < 2 — **pins the deliberate decorative restraint so a later change cannot "fix" contrast by darkening every rule.** `.proof-toggle`, `.input`, `.filing-option` all declare `border: var(--border-control)`. | ✅ |
| W6-T3 | `web/src/styles/styles.test.ts` | `keeps the hidden filing-type radio focusable inside its own row` | `.filing-option` has `position: relative`; `.filing-option input` keeps `opacity: 0` and has neither `display: none` nor `visibility: hidden`; `.filing-option.selected` sets `border-color: var(--color-accent)`. | ✅ |
| W6-T4 | `web/src/styles/styles.test.ts` | `references every declared token and keeps the serif scale at five steps` | For each `--name` in tokens.css, `var(--name)` appears at least once across both stylesheets — **fails today on ten tokens and permanently guards the delete-unreachable-scaffolding rule.** Exactly five `--text-serif-*`; `--color-faint` gone; no `font-size: clamp(` in global.css; `.answer-hero`/`.quote` reference the expected tokens. | ✅ |
| W6-T5 | `web/src/components/components.test.tsx` | `marks the selected filing family without relying on colour alone` **+** metric-table orphan assertion | Replaces the deleted posture test. Render `<FilingTypePicker value="10-K" onChange={vi.fn()} />`; assert the "Annual report" radio is checked, its `.closest(".filing-option")` carries `selected`, `querySelectorAll(".filing-option.selected")` has length 1, and its `.filing-option-check` textContent is `"✓"`. **Additionally** extend the metric-states test to destructure `const { container } = render(...)` and assert `expect(container.querySelector(".trust.missing")).toBeNull()` — this half **does** fail today because `MetricTable` emits `trust missing`. | ✅ |
| W6-T6 | `tests/test_presentation.py` | `test_brief_contract_carries_no_posture_field` | Demo DB, `brief(since=DEMO_SINCE)`. Asserts `"answer_posture" not in models.BriefView.model_fields`, `not hasattr(models, "Posture")`, `not any("posture" in key for key in view.model_dump())`, plus `view.answer == "A tracked company needs a critical review."` to prove the plain-sentence answer is untouched. Add `from finwatch.presentation import models`. | ✅ |

### 7.5 W6 acceptance criteria

1. `uv run pytest -q` green; `cd web && npm test -- --run`, `npm run typecheck`, `npm run build`, `npm run check:classnames` all pass.
2. `npm run check:classnames` exits 0 with an empty problem list, **and is confirmed to fail** (temporarily add a bogus className, observe exit 1, revert).
3. Tabbing through the analysis Drawer shows a visible focus ring on each filing-type option row; tabbing through the dark sidebar rail and the dark setup/sign-in pages shows a visible white-ringed indicator on every link and button.
4. Clicking each filing-option row in the Drawer selects **that row's** option (before the change all four `opacity: 0` radios stacked at the dialog origin).
5. `grep -rn 'posture' src/finwatch tests web/src` returns exactly three hits: `text_policy.py:52`, `:88`, `going_concern.p3.json:4`.
6. `grep -c 'outline' web/src/styles/global.css` is 2; no other focus styling exists.
7. `tokens.css` declares no token without at least one `var()` reference; `global.css` contains no `font-size: clamp(`.
8. At a 375px viewport the brief, companies, company, filing, settings and setup pages have no horizontal overflow, including the CompanyPage date row.
9. With `prefers-reduced-motion: reduce` forced, no new animation or transition is introduced; the focus indicator appears instantly.
10. `diff AGENTS.md CLAUDE.md` empty.

### 7.6 W6 doc updates (both mirrors, same commit)

- **§11**, append one sentence to the first paragraph (after "…not raw HTML or caller-supplied Markdown."): *"The brief carries no posture, tone, or sentiment field: `answer` is a plain sentence and the UI applies no severity-derived colour to it."*
- **§11**, add one bullet at the end of the launch-output list, after "- withheld filings never expose the failed LLM output.": *"- interactive control boundaries and focus indicators are held to WCAG 1.4.11 / 2.4.11 (3:1) by `web/src/styles/styles.test.ts`, while decorative hairlines stay deliberately faint."* Preserve the surrounding punctuation style.
- **§4 "Standing rules"**, add one bullet immediately after the prompts bullet: *"- Every `className` literal under `web/src` must resolve to a selector defined in `web/src/styles/*.css`; `npm run check:classnames` enforces this in CI. Delete the class name or define the rule — never leave an orphan."*

### 7.7 W6 risks

- Raising every interactive border from `rgba(20,35,30,0.19)` to `0.52` is a visible aesthetic change — the app will read as noticeably more "drawn". Required by SC 1.4.11, scoped to controls only, and pinned by W6-T2.
- `.button.ghost` sets `border-color: transparent` and is unaffected by W6-C3, so ghost buttons still have no perceivable boundary. Pre-existing and out of scope — **do not silently assume this workstream closes all of SC 1.4.11.**
- The serif scale collapse changes several headline sizes by 1-3px (h3 20→21, `.filing-link` 24→25, `.holding-title strong` 27→25, `.setup-card h1` max 52→50, `.brief-stats strong` 22→21). Review the brief, companies and setup pages visually before committing; **adjust the five token values rather than reintroducing per-selector sizes.**
- Enabling `css: true` makes vitest process real CSS for every test. Verified locally that all existing tests still pass; a future jsdom-layout-sensitive test could now see real styles.
- Deleting `BriefView.answer_posture` changes the `/api/brief` response shape. The React client is updated in the same commit; a cached bundle from a previous deploy would see the field become `undefined`, which the deleted `answerTone` expression handled by falling through to the empty string.
- The checker cannot see `className={cond ? "a" : "b"}` or class names inside a template hole (why `.trust.missing` is pinned by a unit test instead). Neither conditional form exists today; if one is added later the check silently skips it. Documented in the file header rather than papered over with an allowlist.

---

## 8. Appendix — final DTO shapes (post-W6)

Use these as the drift check after every workstream. If a field is missing, an earlier workstream was applied incompletely.

**`src/finwatch/presentation/models.py`**
```python
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
MetricState = Literal["computed", "unavailable", "not_applicable", "withheld"]
WithheldKind = Literal["gate", "pipeline_failed"]
FilingOutcome = Literal[
    "published", "no_findings", "findings_dropped",
    "withheld_gate", "pipeline_failed", "not_analyzed",
]


class FilingDigestEntry(BaseModel):
    accession: str
    ticker: str
    form: str
    filed: str
    edgar_url: str
    findings: list[FindingView] = Field(default_factory=list, max_length=3)
    withheld: bool = False
    withheld_reason: str | None = None
    withheld_kind: WithheldKind | None = None
    outcome: FilingOutcome = "not_analyzed"
    dropped_finding_count: int = Field(default=0, ge=0)


class IssuerMetricsView(BaseModel):
    ticker: str
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None
    summary: str = ""


class BriefPeriodView(BaseModel):
    covered_label: str
    filings_in_window: int
    analyzed_filings: int
    published_filings: int = 0
    withheld_filings: int = 0
    filings_tracked_total: int = 0
    outside_window: str | None = None


class BriefView(BaseModel):
    period: BriefPeriodView
    tracked_tickers: list[str] = Field(default_factory=list)
    answer: str
    filings: list[FilingDigestEntry] = Field(default_factory=list)
    gate_removed_filings: list[FilingDigestEntry] = Field(default_factory=list)
    verified_numbers: list[IssuerMetricsView] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    reviewed_filings: list[FilingDigestEntry] = Field(default_factory=list)
    withheld_filings: list[FilingDigestEntry] = Field(default_factory=list)
    tracked_but_unanalyzed: bool = False
    filings_synced: int = 0
    disclaimer: str = DISCLAIMER
    sample_data: bool = False


class FilingDetailView(BaseModel):
    filing: FilingDigestEntry
    verified_numbers: IssuerMetricsView | None = None
    verification: VerificationView | None = None
    withheld_reason: str | None = None
    pipeline: list[PipelineStageView] = Field(default_factory=list)
    research: ResearchTraceView | None = None
    certificate_url: str | None = None
    disclaimer: str = DISCLAIMER
    sample_data: bool = False


class CompanyRowView(BaseModel):
    ticker: str
    cik: str
    newest_supported_filing: str | None = None
    compressed_verified_read: str | None = None


class MetricsView(BaseModel):
    ticker: str
    as_of: str
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None
    summary: str = ""
    before_first_filing: bool = False
```

---

## Final. Definition of done

### F.1 Automated verification (run in this order, after each workstream)

```bash
# 1. lint
uv run ruff check .

# 2. backend suite — must be fully green, no xfail added, no test deleted
uv run pytest -q

# 3. frontend
cd web
npm run check:classnames     # W6 onward
npm run typecheck
npm test -- --run
npm run build
cd ..

# 4. doc mirror contract — MUST print nothing
diff AGENTS.md CLAUDE.md
```

### F.2 Grep invariants (all must return nothing unless stated)

```bash
# W1
grep -rn "last_filing" src/ web/src/                       # → nothing
grep -rn "filing(s)" src/finwatch/presentation src/finwatch/digest  # → nothing

# W3
grep -rn "boring_filings\|period\.covered\b\|_boring_section\|Boring filings" src tests web/src

# W4
grep -n 'demo and not remote' src/finwatch/web/app.py
grep -rn '_ANALYZABLE_FORMS' src tests
grep -rn '"10-K", "10-Q", "8-K"' src                       # → only preprocess/forms.py
grep -rn 'guidance-note' web/src

# W5
grep -rn "showComputedMark" web/src src/
grep -rn "exact SEC source" web/src

# W6
grep -rn 'posture' src/finwatch tests web/src              # → exactly 3 known hits
grep -c 'outline' web/src/styles/global.css                # → 2
grep -c 'font-size: clamp(' web/src/styles/global.css      # → 0

# Trust-layer guard — MUST be empty across the whole plan
git diff --name-only main -- \
  src/finwatch/verify/checks.py \
  src/finwatch/verify/compiler.py \
  src/finwatch/metrics/formulas.py \
  src/finwatch/metrics/envelope.py \
  src/finwatch/llm/ \
  src/finwatch/core/types.py
```

> `verify/presentation.py` and `presentation/canonical.py` **are** modified (W1 only) and are covered by W1-T5/T6. `verify/orchestrator.py` is modified by W2. Everything else on the trust-critical list must be untouched.

### F.3 Manual QA script

Run against a local instance (`uv run finwatch serve`) unless a step says hosted.

**QA-1 · Pipeline failure is not a gate refusal (W1).**
1. Track a ticker, sync, then force a failure — e.g. set `filings.status = 'failed'` on the newest supported filing via sqlite.
2. Open `/brief`. Expect: headline "1 filing could not be analyzed — the pipeline did not complete."; a **"Filings that could not be analyzed"** section with the neutral (grey) treatment, not the amber withheld section; the card reads "Analysis did not complete".
3. Open that filing's detail page. Expect the banner "Analysis did not complete" with the `not-analyzed` styling, and **no** occurrence of "gate", "verification did not pass", or "could not be verified" anywhere on the page.
4. Run `finwatch digest` (or the digest CLI path). Expect a `## Filings that could not be analyzed` heading, and no `## Withheld analyses` heading for that filing.

**QA-2 · Gate-removed findings are not an all-clear (W1).**
1. Using the demo DB, patch a verified filing's `P1_TRACE` `dropped_findings` to a non-empty list (as in W1-T2).
2. Open `/brief`. Expect the filing under **"Proposed changes removed by the evidence gate"**, its card reading "N proposed changes were removed by the evidence gate", and the headline **not** reading "Nothing important changed".
3. Confirm the stat tiles do not contradict: "Published N" + "M held back" must be internally consistent.

**QA-3 · Reading window and reviewed filings (W3).**
1. In Settings, narrow the period so the newest tracked filing falls outside it.
2. Open `/brief`. Expect "Reading window" showing a human date range with a "Change in Settings" link; "Filings 0 of N in the window"; a notice naming the ticker, form, human filed date and an "Open settings" link; and **no** "Filings are ready for analysis."
3. Widen the period. Expect a **"Reviewed — nothing material"** section listing routine filings as links. Click one — its detail page must load with ledger, tool count, drop codes, and the certificate download.

**QA-4 · Metric states (W3, W5).**
1. Open a company page for an issuer with no computed metrics but persisted rows. Expect a full table plus a summary line like "6 unavailable of 6 starter metrics" — **not** a single "no verified financials yet" sentence.
2. Corrupt one persisted `result_json` for that issuer. Expect that row rendered as **"— withheld" / "Withheld"** with its own colour, the summary counting it, and **no** trace of the corrupted payload anywhere on screen or in the network response.
3. On `/companies`, that issuer's compressed read must still end in `/6`.
4. Open a **withheld** filing detail page with computed metrics. The status cell must read **"✓ Computed"**, identical to the same row on the company page.

**QA-5 · Verification band (W2).**
1. Open the demo MSFT 10-Q filing page. Expect "What was checked" directly under the outcome banner and above "What changed".
2. Expect V1/V4/V5 under "Blocking — a failure withholds the finding"; V2a–V2d under "Non-blocking — reported, never a gate".
3. Expect the V2c row to show `rev=… gp=… oi=…`; expect **no** detail line on any V1/V4/V5 row.
4. Expect **no** "blocking"/"warning"/"info" severity word rendered anywhere in the band.
5. Open a withheld filing. The band must still render, with a FAIL verdict naming the failed check.

**QA-6 · First run (W4).**
1. Fresh install, no key. Track one ticker, do **not** sync. Open `/brief`. Expect the three-step checklist, step 2 reading "No filings downloaded yet, so there is nothing to analyze", and **exactly one** primary button on the whole page: "Sync filings from SEC".
2. Expect one labelled specimen in "What changed" and one in "Verified numbers", each showing "Example — not your data", containing no links and no ticker/date.
3. Click Sync. Once filings exist, the checklist advances and the header actions return with Sync as primary.
4. Trigger Analyze with nothing to do. Expect one of the three fixed reason messages, never a bare "Analysis completed."
5. **Hosted (if D2 = yes):** log in as a participant with their own ticker, open `/brief?demo=1`. Expect the sample banner, four demo tickers, 3 published filings, 4 findings — and **not** their own ticker. Then open `/brief` and confirm only their own ticker appears.

**QA-7 · Outcome encoding and copy (W5).**
1. Open a filing whose research outcome is `partial`. Expect the **green ✓** glyph and accent wash, and a headline naming both counts.
2. Open a `withheld` filing. Expect the critical-red treatment with the thicker left rule. Open a `metrics_only` filing. Expect the amber default.
3. Expand a certificate with a dropped `DUPLICATE_EVIDENCE` or `EMPTY_HEADLINE` code. Hover the code — a plain-language tooltip must appear.
4. Hover an evidence citation link. It must read "View filing on EDGAR ↗" and open the filing-index page.
5. Find a `share_count_change` row. It must read `±X.X% YoY (direction not certified within SEC rounding slack)` — never "increased"/"decreased"/"flat" — on both the browser and the digest Markdown.
6. Check `/companies`: no row reads "Leverage proxy interest coverage …".
7. Start a sync, then check the job panel: the state line reads a glossed label, never `queued`/`partial`.

**QA-8 · Accessibility (W6).**
1. **Keyboard only.** Tab through `/brief`, `/companies`, `/settings`, the analysis Drawer, and the sign-in / setup pages. Every focusable control must show a visible ring on both the cream surfaces and the dark rail.
2. In the analysis Drawer, tab to the filing-type options — the focused **row** must be outlined. Arrow between options and confirm the selection border/wash moves.
3. **Mouse.** Click each of the four filing-option rows near its top-left. Each must select its own option (this is the stacked-radio bug).
4. **375px viewport.** Load every page. No horizontal scrollbar, including the CompanyPage "Computed as of" date row.
5. **Forced `prefers-reduced-motion: reduce`.** Focus indicators appear instantly; no new animation.
6. Temporarily add `className="does-not-exist"` to any component and run `npm run check:classnames` — it must exit 1 and name the file, line, and class. Revert.

### F.4 Per-commit checklist

For each of the six commits, before pushing:

- [ ] The blocking Decision (D1/D2/D3/D4/D5) for this workstream has an explicit user answer.
- [ ] Every change in the workstream's numbered list is applied, including the cross-referenced merged ones.
- [ ] Every new test named in the workstream table exists and passes.
- [ ] Every listed **existing** test expectation was updated (these are the most common cause of a red suite).
- [ ] Both `AGENTS.md` and `CLAUDE.md` carry the identical doc edits, and `diff AGENTS.md CLAUDE.md` is empty.
- [ ] `SYSTEM_DESIGN.md` updated where the workstream lists it.
- [ ] `uv run ruff check .` clean, `uv run pytest -q` green, frontend typecheck/test/build green.
- [ ] The trust-layer grep guard (F.2) is empty.
- [ ] No `dangerouslySetInnerHTML`, no new dependency, no schema change, no migration.
- [ ] Conventional commit message as specified, including code + tests + both doc mirrors in one commit.