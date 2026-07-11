# RipplX / finwatch — Debug Handoff for a Second Opinion

I'm building an open-source SEC-filing intelligence tool. I've been debugging with an AI
coding assistant and want your independent, adversarial review of three findings and my
proposed fixes. Please push back where I'm wrong. Concrete code-level recommendations welcome.

---

## 0. What I want from you

1. **The big one:** My LLM extractor returns exact SEC quotes *plus* character offsets into a
   filing section. A deterministic verifier requires `section_text[char_start:char_end] ==
   quote` exactly, and **withholds all output if it fails** (fail-closed). It turns out cheap
   models (DeepSeek v4 Flash) return the *verbatim quote* but get the *character offset wrong*
   (LLMs can't count characters). So correct quotes get withheld. **Is my proposed fix — stop
   trusting model-supplied offsets and have the server locate the quote in the declared section
   itself — sound? What are the failure modes (esp. duplicate substrings), and does it preserve
   my trust guarantee?**
2. Best practice for resolving a company's "revenue" across issuers that **migrate XBRL tags**
   over time (e.g. `RevenueFromContractWithCustomerExcludingAssessedTax` → `Revenues`)?
3. Config hygiene: model is set via a `FINWATCH_MODEL` env var read once at process start.
   Should a local web server hot-reload it, or is "restart to apply" the correct norm?

---

## 1. Product & trust model (context)

**finwatch**: track SEC tickers; when the newest 10-K/10-Q/8-K arrives, show at most three
AI-selected qualitative findings, each backed by 1–3 **exact** SEC quotations, plus six
deterministically-computed XBRL metrics. Core promise: **the LLM may select and summarize, but
every number and every quote shown is deterministically verified; if verification can't prove a
quote is exact, the system withholds ALL model output for that filing rather than show anything
unverified.** Metrics come only from SEC XBRL via Python formulas, never the LLM.

Pipeline (5 stages): `download → parse → extract(LLM/P1) → metrics(XBRL) → verify`.
Verifier checks: **V1** numeric provenance, **V4** citation integrity (the exact-offset check),
**V5** schema/hygiene; V2 accounting identities are non-blocking data-quality warnings.

P1 (the one stochastic stage) returns, per finding: a qualitative headline with no digits, and
1–3 evidence items `{section_key, char_start, char_end, snippet}` where `snippet` must equal
`section_text[char_start:char_end]`.

Stack: Python 3.11+, SQLite, FastAPI + React/TS, litellm (OpenAI/OpenRouter prefixes only).

---

## 2. Symptoms observed today

Tested one ticker at a time (AAPL, GOOG, NVDA), model = DeepSeek v4 Flash via OpenRouter:

- **AAPL 10-Q** → *withheld*. Verifier: V4 blocking ×2 ("quote is not exact at declared span")
  + V5 blocking ("extraction reported incomplete input" — model set a non-empty `gaps` field).
- **NVDA 8-K** → *withheld*. Verifier: V4 blocking ×1 ("finding-1-evidence-1: quote is not
  exact at declared span"). 1 finding, 0 gaps, high confidence. Also a V2c data-quality warning.
- **GOOG 8-K** → *boring/clean* (0 findings, nothing to verify → published as "nothing important
  changed"). This is the correct quiet path.
- Metrics (revenue growth, net income, CFO, liquidity, share count, leverage) computed fine
  EXCEPT **revenue growth = unavailable ("stale")** for both GOOG and NVDA.
- After editing `FINWATCH_MODEL` in `.env` from flash→pro, the browser still showed flash and
  the next run still used flash.

---

## 3. Root causes established (with database evidence)

### 3a. Withholding is caused by LLM offset MISCOUNTING, not wrong quotes  ← primary issue

I pulled the withheld NVDA finding and compared its declared span to the section text:

- `section_key = item_5_02`, model-declared offsets `char_start=0, char_end=246`.
- `snippet` = the verbatim "Item 5.02. Departure of Directors…Compensatory Arrangements of
  Certain Officers." header.
- `section_text[0:246] != snippet` → **V4 fails**.
- BUT `section_text.find(snippet) == 0` → **the exact quote IS present at offset 0**; its true
  span is ~0–154. The model over-declared `char_end` (246) by ~92 chars.

So the quote is genuinely verbatim from the correct section; only the model's *character
offset* is wrong. The verifier's exact `[start:end]` slice is what fails. This is a systematic
cheap-model behavior (LLMs cannot reliably count characters), and it withholds otherwise-valid
findings.

**My proposed fix:** treat model offsets as untrusted. In the verifier / canonical projection,
locate `snippet` within its declared `section_key` (`text.find` / regex) and recompute the
offsets server-side; require the quote to appear verbatim (and require it to be **unique** in
the section, else disambiguate by nearest-to-declared or reject). Trust guarantee is preserved
because the quote must still be an exact substring of the named SEC section — I'm only fixing
the pointer, not the text. This would let weaker/cheaper models pass. Even stronger: have P1
return only `{section_key, exact_quote}` and never ask the model for offsets at all — the server
computes them. **Please critique this, especially the duplicate-substring / ambiguity risk and
whether re-anchoring undermines "the model can't move the goalposts."**

The exact check lives in:
- `src/finwatch/presentation/canonical.py` → `_exact_evidence()` (`section.text[start:end] == snippet`)
- `src/finwatch/verify/checks.py` → `check_v4_citations()` (same slice comparison)

### 3b. Model won't switch to "pro" = env cached at process start (NOT hardcoded)

- `src/finwatch/web/runtime.py` → `production_model()` returns `os.environ.get("FINWATCH_MODEL")`.
- The web server loads `.env` once at startup (`load_dotenv()` in the `serve` CLI command, which
  uses `os.environ.setdefault`, so real env wins). Editing `.env` afterward does not reach the
  running process. DB confirms the run used `deepseek-v4-flash` despite `.env` saying pro.
- No model is hardcoded anywhere except the offline demo (`demo/recorded`).
- The browser's model display and the `analysis_configured` gate both derive from this same env
  value (`app.py`: `"model": settings.model`, `analysis_configured = bool(model and key)`), so
  env already IS the single source of truth — it just isn't hot-reloaded.
- **Question:** hot-reload `FINWATCH_MODEL` from `.env` on each request, or keep restart-required?

### 3c. "Stale revenue" = XBRL revenue-tag migration not handled

- NVDA companyfacts is current (facts through 2026-04-26).
- NVDA's revenue is under tag `Revenues` (current, →2026-04-26). It **abandoned**
  `RevenueFromContractWithCustomerExcludingAssessedTax` after FY2022 (newest = 2022-01-30).
- The metric resolves "revenue" to `RevenueFromContractWithCustomerExcludingAssessedTax`, whose
  newest annual leg is FY2022 → 1623 days old → exceeds the 550-day annual-freshness limit →
  marked `unavailable`. (GOOG shows the same symptom but only 557 days / 7 days over — GOOG may
  simply not have filed FY2025 yet, or may also have a fresher `Revenues` tag; unconfirmed.)
- Net income / CFO compute fine because their tags didn't migrate.
- Cascade: the stale revenue ($26.9B FY2022) vs current gross profit/operating income ($153B/
  $130B) violates the V2c identity (Rev ≥ GP ≥ OI) → a non-blocking data-quality warning
  ("a deterministic data-quality check needs review").
- Resolution logic lives in `src/finwatch/xbrl/normalize.py` (FactStore concept resolution).
- **Question:** robust revenue-concept resolution across tag migrations (`Revenues`,
  `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, …) with correct
  period alignment and no double counting?

---

## 4. Changes already made this session (for context; not the question)

1. Publication gate now tolerates *non-blocking warnings* on required checks (a routine filing
   with only warnings is "boring", not "withheld"); displayed quotes are still re-verified exactly.
2. Relabeled misleading "pending manual review" UI text → "could not be verified" (there is no
   manual-review feature; withheld filings are terminal unless re-analyzed).
3. Added a small reset+re-analyze script to A/B two models on the same filing.
4. Created `.env` (was missing), gitignored it (it wasn't), and made `serve` read `.env`.

---

## 5. The core tension I want you to weigh in on

The whole product is built on "fail closed — never show an unverified quote." That's good. But
the thing failing (character offsets) is a model artifact the model is *bad at*, while the thing
that matters for trust (the quote is an exact substring of the real SEC section) is *satisfied*.
Am I right that re-anchoring offsets server-side is the correct fix (and arguably the model
should never supply offsets at all), or is there a subtle trust hole I'm missing? And where's the
line — should I also auto-repair anything else the model gets mechanically wrong, or does that
start eroding the "deterministic" guarantee?
