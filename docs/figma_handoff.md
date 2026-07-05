# finwatch — Figma Make Build Prompt

Build a complete React + TypeScript prototype for **finwatch**, a filing-intelligence web app. Follow this spec exactly. Use realistic seeded mock data (below) so the prototype looks fully alive with no backend. Prioritize calm, restraint, and trustworthiness over density or flourish.

---

## 1. Product & Audience

finwatch is open-source filing intelligence for **self-directed investors** — hedge-fund analysts, family-office PMs, and sell-side researchers who are financially fluent but **not programmers**. It watches their holdings, reads new SEC filings (10-K/10-Q/8-K), highlights the few that materially changed, checks every number deterministically against SEC XBRL data, and shows **why it matters — with citations to the exact filing text**. The north-star user pain: *"I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something actually important changed."* This is **not an investment advisor**: it ships **review postures** (critical_review / risk_review / monitor / positive_support / insufficient_data), never trade instructions or price targets. Because the whole reason this app exists is that a terminal is friction for these users, the UI must be minimalist, calm, key-ideas-first, low-noise, scannable, and trustworthy. Fewer sharper alerts beat more alerts; **silence on boring filings is a feature**; honest `not_applicable` / `insufficient_data` states are first-class, never errors.

---

## 2. Design Direction & Tokens (use VERBATIM)

Warm-white fintech. One accent. Flat fills only. Hairlines, not boxes-with-shadows. This token set was authored for presentation slides — **translate it faithfully to app density**: keep the palette, the three fonts, the flatness, and the hairline language byte-exact; scale the type ramp and spacing down (~0.55–0.6×) while preserving the hierarchy and rhythm.

### Color (verbatim)
```
--color-bg:          #FCFCFA;  --color-panel:       #F1F0EA;  --color-panel-alt:   #F4F3EE;
--color-ink:         #18211D;  --color-body:        #3C443E;  --color-muted:       #5C645E;
--color-faint:       #8A8F88;  --color-faint-2:     #A6ABA4;  --color-hairline:    #E4E2DA;
--color-accent:      #0E7C66;  /* teal-green — positive/working/brand */
--color-accent-wash: rgba(14,124,102,0.10);
--color-warn:        #BC6B2E;  /* clay-amber — in-progress/attention */
--color-warn-wash:   rgba(188,107,46,0.12);
```

### Type
```
--font-serif: 'Spectral', serif;        /* weights 400/500/600 + italic 400 */
--font-sans:  'IBM Plex Sans', sans-serif; /* 400/500/600 */
--font-mono:  'IBM Plex Mono', monospace;  /* 400/500 */
```
Google Fonts to load: **Spectral** (ital 400 + 400/500/600), **IBM Plex Sans** 400/500/600, **IBM Plex Mono** 400/500.

### Type ramp — translated to APP density (use these px values)
```
--text-display: 40px;  /* setup hero + answer-hero ONLY */
--text-h1:      32px;   --text-h2-italic: 26px (Spectral italic);
--text-lede:    21px;   --text-body-lg: 17px;  --text-body: 16px;  --text-body-sm: 15px;
--text-kicker:  12px;   --text-caption: 13px;
--tracking-kicker: 0.16em; --tracking-badge: 0.06em; --tracking-tight: -0.01em;
```
Hierarchy is preserved from the slide ramp; only the scale shrank. Body is ~16px, not 24px. Call this translation out in code comments.

### Shape & spacing (app density)
```
--radius-pill: 999px; --radius-lg: 14px; --radius-md: 12px; --radius-sm: 8px;
--border-hair: 1px solid var(--color-hairline);  --border-strong: 2px solid var(--color-ink);
--space-page-x: 56px; --space-page-y: 44px; --space-section: 40px; --space-item: 14px;
```

### Five visual principles (non-negotiable)
1. **Color signals meaning, nothing else.** Teal = positive/working/computed/monitor. Amber = attention/in-progress/risk. No other hue anywhere.
2. **Serif for feeling, sans for scanning, mono for machine truth.** Spectral for headlines, the "what changed" prose, and verbatim filing quotes; Plex Sans for body/labels/UI chrome; Plex Mono for every number, %, ratio, formula version, accession number, and EDGAR link.
3. **Flat fills only** — no shadows, no gradients. The single exception is one subtle radial watermark on the setup/empty cover.
4. **Hairlines separate content**, not shadowed cards.
5. **Badges are pill-shaped, uppercase, mono-tracked, tinted wash** of their status color.

### Status-color mapping for filing postures (explain + obey)
Map the product vocabulary onto the two accents only. **There is no red.** Critical findings use `--color-ink` weight plus amber wash — **restraint over alarm**: a going-concern must read as *gravity*, not a fire alarm, because these users distrust anything that looks like a screaming trading terminal. Trust is built by calm.

| Meaning | Color |
|---|---|
| `positive_support`, `monitor`, computed ✓, "working" | teal (`--color-accent` / `--color-accent-wash`) |
| `risk_review`, attention, sync/analysis in-progress, `HIGH`/`MEDIUM` severity | amber (`--color-warn` / `--color-warn-wash`) |
| `critical_review`, `CRITICAL` severity | ink text on amber wash — never red |
| `insufficient_data`, `LOW`, `unavailable`, boring | muted neutrals (`--color-muted`/`--color-faint`/`--color-panel`) |

---

## 3. Global Layout & Navigation

- **Slim persistent left rail** (never tab clutter). Exactly **3 destinations + a settings gear**:
  1. **The Brief** — home / default. The digest as an inbox.
  2. **Holdings** — owned + watch-only management.
  3. **Track record** — the shadow-signal audit.
  4. **⚙ Settings** (gear at rail foot) — SEC User-Agent identity, model-key status, the master `--signals` toggle (off by default), period default.
- **Reading/ops controls live WITH content, never in nav.** The period range control, "Sync filings", and "Run analysis" sit in The Brief header (and per-company). The shadow-signals toggle sits at the **foot of The Brief** and inside filing/company detail — it is a reading *mode* and a per-company tab, never a nav item.
- **First run** intercepts with a Setup gate, then drops the user into a live (possibly empty) Brief or the bundled demo.
- **Add / Watch / Sync / Run-analysis open as side panels**, not modal traps — the feed/list stays visible behind them. Back always restores The Brief's scroll position.
- Persistent **verbatim disclaimer footer** on every digest/detail surface.

---

## 4. Screen-by-Screen Spec

Bind every screen to the deterministic digest section order (fixed, load-bearing — never reorder or rename): **Header → Critical red flags → What changed → Thesis impact → Verified numbers → Open questions → Boring filings → Shadow signals (toggle only) → hairline + disclaimer.**

### Screen 1 — First-run Setup + demo entry (zero-key)
**Purpose:** Capture the required SEC User-Agent identity and explain *why*, while offering the bundled demo so a keyless, non-technical user sees real output in under 60s.
**Content:** Serif display hero = the north-star pain quote verbatim: *"I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something actually important changed."* Sub-line (Plex Sans): *"Open-source filing intelligence — watches your holdings, reads new SEC filings, highlights material changes, checks every number deterministically, and shows why it matters, with citations."* One required field: **SEC User-Agent** (mono placeholder `you@example.com`) with plain-English note: *"EDGAR asks every reader to identify themselves; finwatch throttles to ≤8 requests/sec. No account, no API key needed to start."* Two CTAs: primary teal **Continue**, secondary ghost **See the demo brief** (loads bundled fixture digest, labelled "sample data"). The one permitted **radial watermark** lives here. Disclaimer footer.
**States:** *empty* — fields blank, demo always available. *error* — missing/invalid User-Agent → inline amber hairline note, never a red alarm. *loading* — demo shows a brief skeleton while the fixture loads.

### Screen 2 — The Brief (digest home / inbox) [HOME]
**Purpose:** The main surface. A calm, scannable feed of what changed this period, in fixed digest order. Zero-key: renders whatever analysis exists.
**Content, top to bottom:**
- **Answer-hero** (one Spectral sentence at the very top, tinted by the most-severe posture): e.g. ink-weight *"One holding needs a critical review."* — or calm *"Nothing important changed. 5 routine filings reviewed."*
- **Header block** (3 mono metadata lines): `Period covered: 2024-08-01 → now` · `Holdings tracked: DPLS, MSFT  ·  watching: AAPL, TWKS` (owned bold/ink, watch-only muted with a small `WATCH` tag) · `Filings in window: 5`. Serif title `finwatch digest`. Period range control + **Sync filings** action live in this header.
- **§1 Critical red flags** (feed-top, most prominent). Cards headed `### DPLS — 10-K filed 2024-08-02 · CRITICAL · critical_review` (owned) and `### TWKS — 8-K filed 2024-08-02 · CRITICAL · watch — company-level read, no signal` (watch-only). Each lists material-item headlines with *italic event_type tags* (e.g. *Going concern doubt (going_concern)*), then red-flag rows: **flag label** + severity chip + prominent **EDGAR ▸** link + serif-italic curly-quoted verbatim snippet (`"raise substantial doubt about its ability to continue as a going concern"`). The EDGAR link + quote are the trust anchor — visually prominent, clickable, cards drill into Filing detail.
- **§2 What changed.** Card `### DPLS (direct) — via DPLS 10-K 2024-08-02` + one Spectral prose paragraph (the P2 net_read). Then a **Channels** row (up to 7: revenue, margins, capital structure, cash/working capital, competitive position, governance, cross-holding spillover), each `label (direction, magnitude)` e.g. `revenue (negative, major)`. Then one line `Guidance: none_stated · Liquidity: deteriorating · Net: negative`. Optional `Risk-factor changes: N added, N removed, N modified`.
- **§3 Thesis impact.** Terse bullets, one per owned position: `**DPLS:** thesis broken`. Graceful-degradation note when no thesis was provided.
- **§4 Verified numbers.** Intro caption verbatim: *"Computed by versioned deterministic formulas from SEC XBRL facts (never by the LLM) and traceable to those facts. ✓ = a computed value; — = not applicable or data missing."* Then per owned issuer a `### MSFT` subhead + the **4-column table `Metric | Value | Formula | ✓`** with the 6 starter rows (see sample data). Values + formula versions in mono, ✓ teal.
- **§5 Open questions.** Honest-gap bullets: `MSFT: rule M6 not evaluated — valuation percentiles computed=0, need 2`.
- **§6 Boring filings.** ONE low-emphasis collapsed line: `3 routine filing(s) with no material findings (AAPL 8-K, MSFT 10-Q, AAPL 10-Q).` Omitted entirely when zero.
- **Foot:** the shadow-signals toggle (off), then hairline + verbatim disclaimer.
**States:** *empty (tracked, nothing analyzed)* — header + verified-numbers + boring still render, plus a gentle *"No material findings yet — Sync filings or Run analysis,"* never a dead-end. *empty (nothing tracked)* — guide to Holdings. *loading/sync* — inline per-ticker progress in header. *insufficient_data* — posture pill renders `insufficient_data` in muted neutral. *boring-only window* — answer-hero says "Nothing important changed"; §1/§2 render their verbatim empty strings (*"None. No critical or high-severity findings in this window."* / *"No portfolio-relevant transmission analysis in this window."*) as faint italic serif lines, and the boring line carries the weight. *error* — amber banner, cached content still shown.

### Screen 3 — Filing detail (digest item drill-down)
**Purpose:** Open one digest item like an email — full evidence and trust artifact for a single filing.
**Content:** Header `DPLS — 10-K filed 2024-08-02` with severity badge (CRITICAL, ink+amber) and, for owned, the review-posture pill; for watch-only the literal `watch — company-level read, no signal`. Material items with italic event_type tags. **Red-flags block:** each row = human-labelled flag (one of the 8 CRITICAL_DOC_FLAGS mapped to a label) + severity chip + prominent **EDGAR ▸** link + full serif-italic curly-quoted verbatim span. **Transmission channels** (up to 7) + the Guidance/Liquidity/Net line + optional risk-factor-changes line. **Thesis** (owned only). **Verified numbers** for this issuer (starter table, link to full Company view). **Audit strip:** a quiet **Re-verify** affordance → overall verdict `PASS / PASS_WITH_WARNINGS / FAIL` + per-check line items `V1: PASS · V4: PASS · V5: PASS` for the accession. Shadow-signal panel appears here **only if** the global toggle is on, visually quarantined.
**States:** *insufficient_data* — muted pill + reason. *no red flags* — "No critical or high-severity findings." *verify FAIL* — `⚠ manual review required` banner (ink+amber). *watch-only* — thesis + posture/signal suppressed, replaced by "company-level read, no signal." *missing XBRL* — verified block shows "no verified financials yet (XBRL facts insufficient or not yet ingested)."

### Screen 4 — Holdings (owned + watchlist)
**Purpose:** Manage the portfolio The Brief reads from. Make **owned vs watch-only a first-class visual split.**
**Content:** Two grouped lists. **Owned holdings** rows: ticker (serif) + shares + cost basis + optional target-weight % (mono), latest posture pill, last-filing date, a subtle thesis-present indicator, and a **compressed one-line verified read** in mono (`Rev +17.5% · Leverage 0.32× · ✓6/6`) or the honest `no verified financials yet` line. **Watch-only** rows: ticker + small `WATCH` tag + muted caption `company-level read, no signal`, latest severity chip if any — **explicitly no posture pill.** Rows sort most-cautious first (attention-ordered), owned above watch-only. Header actions: **Add holding** (primary teal), **Watch a company** (secondary), **Sync filings** (per-ticker progress: filings indexed/new, XBRL facts, prices). Each row drills to its Company view.
**States:** *empty* — "Add a holding or watch a company to start your brief" + both CTAs. *sync in progress* — per-ticker amber progress rows. *error* — `TickerNotFoundError` surfaced inline on the offending row. Watch-only rows never render a signal chip.

### Screen 5 — Add holding / Watch (side panel, segmented)
**Purpose:** The two write actions. **Thesis is optional by design — never gate onboarding.**
**Content:** Segmented control at top: **Add holding (I own this)** vs **Watch (track, no signal)**. Add path: Ticker (required), Shares (required, mono), Cost basis/share (required, mono), then a hairline-divided *Optional* block: Target weight %, Horizon dropdown (`trading | 1-3y | 5y+ | indefinite`), Thesis free-text (helper: *"Optional — finwatch degrades gracefully without a thesis; you can add one later"*). Watch path: just Ticker + one line *"Watch = company-level read, no position, no signal."* On success: toast *"Added DPLS — Sync filings to pull its 10-K/10-Q/8-K."*
**States:** *validation* — unknown ticker → inline amber "Ticker not found on EDGAR." *submitting* — button spinner. *missing optional fields* — allowed, no warning, rendered as calm `—`.

### Screen 6 — Company · Verified numbers (flagship zero-key trust surface)
**Purpose:** Show a single issuer's deterministic SEC-XBRL numbers with point-in-time control and the full catalog on demand. "See the trust layer work" without any API key.
**Content:** Header: ticker (serif) + owned/watch tag. Intro caption verbatim (as §4). The **4-column table `Metric | Value | Formula | ✓`**, starter set of 6 by default (sample data). Controls: an **as-of date picker** (mono, point-in-time — a historical run never shows a later-filed restatement) and a **Show all metrics** toggle (reveals full catalog: Altman-Z, Piotroski-F, Beneish-M, valuation percentiles, below a hairline, labelled "extended catalog — not in the digest"). The **✓ vs — column is preserved as THREE distinct states**: ✓ = computed, — with "not applicable for this issuer" (e.g. a bank on EV/EBITDA), — with "data missing" (unavailable). Never collapse to a blank.
**States:** *insufficient XBRL / foreign private issuer* — single honest line "DPLS: no verified financials yet (XBRL facts insufficient or not yet ingested)." *as-of before first filing* — empty with explanation. *loading* — skeleton rows.

### Screen 7 — Track record (shadow audit, zero-key)
**Purpose:** Frame the whole signal engine as shadow-mode / off-by-default; build trust precisely by NOT acting on signals.
**Content:** Header `Shadow-signal track record (N evaluations)` with mandatory unvalidated/educational framing up top. Two count groups as muted chips: **Review postures** (`monitor=… critical_review=… risk_review=… positive_support=… insufficient_data=…`) and **Hypothetical signals** (`HOLD=… STRONG_REVIEW_SELL=… TRIM=… ACCUMULATE=…`). `Outcomes reviewed: 0/N`. Promotion policy verbatim: *"Signals are UNVALIDATED educational shadow output. Promotion requires ≥100 logged evaluations, a human audit of ≥20 sampled cases, and passing the acceptance gates."*
**States:** *empty* — "No evaluations logged yet — shadow signals accrue as owned holdings are analyzed." Always shows the unvalidated banner even when populated.

### Screen 8 — Shadow signals (opt-in reading MODE, not a nav screen)
**Purpose:** The one place trade-ish vocabulary appears — inline within The Brief and Filing detail **only when the explicit off-by-default toggle is on**, always visually quarantined.
**Content:** Opens with the mandatory verbatim blockquote banner: *"⚠ Unvalidated shadow output — educational only, not a trade instruction. These hypothetical signals are logged to build an auditable track record; they are off by default and shown only with --signals."* Cards: `### MSFT — hypothetical signal: HOLD (posture monitor)` with bullets **Rules fired** (`M8`), **Rationale**, **Counter-evidence** (mandatory), **What would change this** (semicolon list). Critical example: `### DPLS — hypothetical signal: STRONG_REVIEW_SELL (posture critical_review)` — `Rules fired: M1, M1:going_concern, M1:material_weakness_with_restatement_risk`. The whole region sits inside a distinct hairline-bordered, tinted-neutral quarantine container, walled off from the safe posture surface. The SIGNAL word renders in ink weight + amber wash, never celebrated.
**States:** *toggle off (default)* — entirely absent (gone, not greyed). *P3 prose failed verification* — rationale withheld with a warning line instead of fabricated text. *no owned holdings* — absent (P3 runs owned-only).

### Screen 9 — Run analysis (the ONLY key-gated flow)
**Purpose:** Run the LLM pipeline over ingested-but-unanalyzed filings. The single place a key is needed; must degrade gracefully and **never block the rest of the app.**
**Content:** A **Run analysis** button (global on The Brief + per-company). If **no model key:** a calm panel, not an error — *"Analysis needs a language-model key (FINWATCH_MODEL_EXTRACT and FINWATCH_MODEL_REASON) to read filings. Everything else — verified numbers, the demo brief, your holdings — works without one. No key is needed for the demo."* + a **See the demo brief** fallback. If key present: async per-filing progress, each result showing verdict `PASS / PASS_WITH_WARNINGS / FAIL` or `⚠ manual review required`. Scope selector: whole portfolio or single ticker.
**States:** *no-key* — graceful explainer + demo offer, rest of app untouched. *running* — async progress list. *per-filing FAIL* — `⚠ manual review required`, digest still renders others. *partial* — completed analyses appear in The Brief immediately.

### Screen 10 — Settings
SEC User-Agent identity, optional model keys (labelled "unlocks Run analysis"), the **`--signals` master toggle** (off by default, labelled unvalidated/educational), period default. Zero-key except the model-key fields.

---

## 5. Component Inventory (variants + states)

Build **one component per file**. Specify all:

- **AnswerHero** — one Spectral sentence, weight/tint set by most-severe posture (ink for critical, teal for calm/positive, amber for attention). Home only.
- **PosturePill** — pill, uppercase-underscore, mono-tracked (`--tracking-badge`), wash-tinted. Variants: `positive_support`/`monitor` = teal wash; `risk_review` = amber wash; `critical_review` = ink text on amber wash (never red); `insufficient_data` = neutral/muted wash. **Five values only** — never invent synonyms.
- **SeverityBadge** — pill, mono uppercase. `CRITICAL` (ink+amber), `HIGH` (amber), `MEDIUM` (amber wash/muted), `LOW` (faint neutral).
- **OwnedWatchTag** — tiny mono uppercase chip. `OWNED` = ink, `WATCH` = muted, always paired with "company-level read, no signal" caption on watch-only.
- **FilingItemCard** — the inbox unit: `TICKER — FORM filed DATE · SEVERITY · posture-or-watch-label`, clickable into Filing detail.
- **RedFlagRow** — human-labelled flag (mapped from the 8 CRITICAL_DOC_FLAGS: `item_1_03_bankruptcy` → "Bankruptcy", `item_3_01_delisting` → "Delisting", `item_2_04_acceleration` → "Debt acceleration", `item_4_02_non_reliance` → "Non-reliance on prior financials", `going_concern` → "Going-concern doubt", `auditor_resignation` → "Auditor resignation", `material_weakness_with_restatement_risk` → "Material weakness", `cyber_1_05_critical_tier` → "Critical cyber incident") + severity chip + **EDGAR ▸** link + serif-italic curly-quoted verbatim span. The load-bearing trust anchor.
- **EdgarCitationLink** — mono, external-arrow affordance, **always attached to a verbatim quote span; never a bare URL.**
- **VerifiedNumbersTable** + **MetricRow** — 4 columns `Metric | Value | Formula | ✓`. Value + Formula in mono. Horizontally scrollable in its own `overflow-x:auto` container on narrow screens.
- **FormulaVersionChip** — inline mono token e.g. `` `revenue_growth.v1` ``; signals "deterministic, versioned, not LLM-authored."
- **TrustGlyph** (✓ / —) — teal ✓ = computed; em-dash = not_applicable vs unavailable (distinct via hover/label). **Three states, never blank.**
- **CompressedVerifiedRead** — Holdings-row one-liner: `Rev +17.5% · Leverage 0.32× · ✓6/6`.
- **ChannelRow** — `label (direction, magnitude)`, grouped across up to 7 channels.
- **GuidanceLiquidityNetLine** — single mono-accented line.
- **ThesisVerdictBullet** — `{TICKER}: thesis {verdict}` + graceful-degradation note variant.
- **SectionKicker** — uppercase mono-tracked kicker + serif title + hairline underneath; enforces fixed section order.
- **BoringLine** — one low-emphasis line; omitted at zero.
- **OpenQuestionBullet** — muted honest-gap line (P1 gaps, P3 skipped-rule reasons, V2 data-quality warnings).
- **EmptyStateBlock** — renders the verbatim per-section strings as designed states, not hidden sections.
- **ShadowToggle** — off-by-default switch, labelled "Show shadow signals (unvalidated, educational)."
- **ShadowQuarantineCard** — hairline-bordered tinted-neutral container + mandatory banner + hypothetical-signal block.
- **AsOfDatePicker**, **ShowAllMetricsToggle**, **SyncProgressIndicator** (per-ticker), **VerifyAuditStrip** (V1/V4/V5 + verdict), **KeyRequiredNotice** (calm degradation panel), **HeaderMetadataBlock** (3 mono lines, owned bold / watch muted), **AddHoldingForm** / **WatchForm** (segmented, thesis optional, inline TickerNotFoundError), **DisclaimerFooter** (verbatim, persistent), **RadialWatermark** (cover/empty only).
- **MetricValueFormatter** util — explicit `+`/`−` sign; USD auto-scaled K/M/B/T with one decimal; `×` for ratios; "4-quarter direction rising/mixed/falling."

---

## 6. Interaction & Motion

Minimal and calm. Hairline dividers, never shadowed cards. Transitions ≤150ms, ease-out, opacity/position only — no bounce, no scale-pop. Side panels slide in from the right over a faint scrim; back restores scroll position. Row hover = a subtle `--color-panel` fill, not elevation. Async ops (sync, run-analysis) show inline per-ticker progress rows in amber, never spinners-that-block. Toggling shadow mode reveals/hides its section in place; it never navigates. No auto-playing motion, no skeleton shimmer beyond a quiet opacity pulse.

---

## 7. Responsive & Accessibility

- **Desktop-first**, graceful down to tablet/mobile. Left rail collapses to a slim top bar under ~900px. The 4-column verified-numbers table scrolls inside its own `overflow-x:auto` container — the page body never scrolls horizontally. Multi-column sections stack to single column on mobile; the answer-hero and header metadata reflow but keep hierarchy.
- **Contrast:** all text meets WCAG AA on `--color-bg`/`--color-panel`. Never rely on color alone — postures/severities always carry a text label alongside their wash.
- **Focus:** visible 2px `--color-accent` focus ring on every interactive element; logical tab order; Esc closes side panels.
- **Semantic:** proper landmarks (`nav`, `main`, `header`, `footer`), real `<table>` for verified numbers, `<button>`/`<a>` (EDGAR links are real `<a target="_blank" rel="noopener">`), `aria-live="polite"` on sync/analysis progress, headings in order.

---

## 8. Tech Constraints for Figma Make

- **React + TypeScript.** One component per file. Typed props; a shared `types.ts` for postures, severities, flag codes, metric envelope (`computed | unavailable | not_applicable`).
- **Design tokens as CSS custom properties** in a single `tokens.css` (`:root { … }`) — list exactly the variables in §2 (colors, fonts, type ramp, shape, spacing). Reference tokens only; no hardcoded hex.
- **No shadows, no gradients** anywhere (one radial watermark excepted). Flat fills + hairlines only.
- **Three fonts only** — Spectral, IBM Plex Sans, IBM Plex Mono (Google Fonts).
- **Realistic seeded mock data** in a `mockData.ts` fixture so the prototype renders fully with no backend — no fetch, no API layer. All state is local. The demo digest is the default seeded content.
- No routing library needed — a simple in-memory view switcher keyed to the 3 rail destinations + detail overlays is fine.

---

## 9. Sample Data (seed the prototype with this)

```json
{
  "period": { "covered": "2024-08-01 → now", "filingsInWindow": 5 },
  "portfolio": {
    "owned": ["DPLS", "MSFT"],
    "watching": ["AAPL", "TWKS"]
  },
  "criticalRedFlags": [
    {
      "ticker": "DPLS", "owned": true, "form": "10-K", "filed": "2024-08-02",
      "severity": "CRITICAL", "posture": "critical_review",
      "materialItems": [
        { "headline": "Going concern doubt", "eventType": "going_concern" },
        { "headline": "Material weakness in controls", "eventType": "material_weakness" }
      ],
      "flags": [
        { "code": "going_concern", "label": "Going-concern doubt", "severity": "CRITICAL",
          "edgarUrl": "https://www.sec.gov/Archives/edgar/data/866439/000168316824004848/darkpulse_i10k-123123.htm",
          "quote": "raise substantial doubt about its ability to continue as a going concern" },
        { "code": "material_weakness_with_restatement_risk", "label": "Material weakness", "severity": "HIGH",
          "edgarUrl": "https://www.sec.gov/Archives/edgar/data/866439/000168316824004848/darkpulse_i10k-123123.htm",
          "quote": "material weakness in internal control over financial reporting" }
      ]
    },
    {
      "ticker": "TWKS", "owned": false, "form": "8-K", "filed": "2024-08-02",
      "severity": "CRITICAL", "watchLabel": "watch — company-level read, no signal",
      "materialItems": [{ "headline": "Non-reliance on prior financials; restatement", "eventType": "non_reliance" }],
      "flags": [
        { "code": "item_4_02_non_reliance", "label": "Non-reliance on prior financials", "severity": "CRITICAL",
          "edgarUrl": "https://www.sec.gov/Archives/edgar/data/...",
          "quote": "should no longer be relied upon due to an error in revenue recognition" }
      ]
    }
  ],
  "whatChanged": [
    {
      "ticker": "DPLS", "impactClass": "direct", "via": "DPLS 10-K 2024-08-02",
      "netRead": "The auditor's going-concern paragraph and the disclosed material weakness go to the core of the turnaround thesis: together they question whether the company can keep operating and whether its own reported figures can be relied upon. This is a load-bearing contradiction of the reason to own the stock, not a soft quarter.",
      "channels": [
        { "label": "revenue", "direction": "negative", "magnitude": "major" },
        { "label": "capital structure", "direction": "negative", "magnitude": "major" },
        { "label": "cash/working capital", "direction": "negative", "magnitude": "major" },
        { "label": "governance", "direction": "negative", "magnitude": "moderate" }
      ],
      "guidance": "none_stated", "liquidity": "deteriorating", "net": "negative"
    }
  ],
  "thesisImpact": [{ "ticker": "DPLS", "verdict": "thesis broken" }],
  "verifiedNumbers": {
    "MSFT": {
      "owned": true,
      "rows": [
        { "metric": "Revenue growth", "value": "+17.5% YoY (TTM revenue $205.1B)", "formula": "revenue_growth.v1", "state": "computed" },
        { "metric": "Net income trend", "value": "+38.4% YoY · 4-quarter direction mixed", "formula": "net_income_trend.v1", "state": "computed" },
        { "metric": "Operating cash flow", "value": "+26.5% YoY · 4-quarter direction mixed", "formula": "cfo_trend.v1", "state": "computed" },
        { "metric": "Liquidity", "value": "cash $19.6B · net debt $25.3B · current ratio 1.24", "formula": "liquidity_basics.v1", "state": "computed" },
        { "metric": "Share count Δ", "value": "−0.6% YoY (buyback)", "formula": "share_count_change.v1", "state": "computed" },
        { "metric": "Leverage", "value": "net debt/EBITDA 0.32× · interest coverage 29.80×", "formula": "simple_leverage.v1", "state": "computed" }
      ]
    },
    "AAPL": {
      "owned": false,
      "rows": [
        { "metric": "Revenue growth", "value": "+6.4% YoY (TTM revenue $444.3B)", "formula": "revenue_growth.v1", "state": "computed" },
        { "metric": "Leverage", "value": "net debt/EBITDA 0.26× · interest coverage 33.83×", "formula": "simple_leverage.v1", "state": "computed" }
      ]
    },
    "DPLS": { "owned": true, "empty": "no verified financials yet (XBRL facts insufficient or not yet ingested)." }
  },
  "openQuestions": [
    "MSFT: rule M6 not evaluated — valuation percentiles computed=0, need 2",
    "MSFT: rule M7 not evaluated — thesis_verdict=not_assessable",
    "MSFT: rule M5 not evaluated — weights unavailable"
  ],
  "boringFilings": "3 routine filing(s) with no material findings (AAPL 8-K, MSFT 10-Q, AAPL 10-Q).",
  "insufficientDataExample": {
    "ticker": "AAPL", "form": "8-K", "filed": "2024-07-30",
    "posture": "insufficient_data",
    "reason": "extraction confidence low; gaps block assessment"
  },
  "shadowSignals": [
    {
      "ticker": "MSFT", "signal": "HOLD", "posture": "monitor",
      "rulesFired": ["M8"],
      "rationale": "No document-level red flags fired and no rule crossed a review threshold, so the matrix defaults to HOLD (rule M8). The quarter reads as routine, which is exactly the kind of filing the system is designed to stay quiet about.",
      "counterEvidence": "A clean quarter is not the same as a cheap or de-risked one; a routine filing can still sit on top of a stretched valuation or a slowing end market.",
      "whatWouldChangeThis": ["A later filing that introduces a red flag or a guidance cut", "Deterioration in the verified financials", "The position drifting materially above its target weight"]
    },
    {
      "ticker": "DPLS", "signal": "STRONG_REVIEW_SELL", "posture": "critical_review",
      "rulesFired": ["M1", "M1:going_concern", "M1:material_weakness_with_restatement_risk"],
      "rationale": "A document-level critical red flag fired (rule M1): the auditor expressed substantial doubt about the company's ability to continue as a going concern, and management disclosed a material weakness in internal control over financial reporting.",
      "counterEvidence": "A going-concern paragraph is a disclosure about risk, not a bankruptcy filing; companies do sometimes raise capital or restructure and recover.",
      "whatWouldChangeThis": ["A later filing in which the auditor removes the going-concern qualification", "Auditor-confirmed remediation of the material weakness", "A financing or restructuring that removes near-term solvency risk"]
    }
  ],
  "verifyExample": { "accession": "0000866439-24-004848", "verdict": "PASS_WITH_WARNINGS", "checks": ["V1: PASS", "V4: PASS", "V5: PASS"] },
  "disclaimer": "Educational analysis of public information for the portfolio owner's own decision-making. Not individualized investment advice. Data may be incomplete or delayed."
}
```

**Vocabulary — render exactly, never invent synonyms:** Postures (user-facing, lowercase_underscore): `critical_review, risk_review, monitor, positive_support, insufficient_data`. Hypothetical signals (shadow-only, UPPERCASE): `STRONG_REVIEW_SELL, TRIM, HOLD, ACCUMULATE`. Severities (UPPERCASE): `CRITICAL, HIGH, MEDIUM, LOW`. Preserve casing exactly for chip/badge fidelity.

---

## 10. Scope & Non-Goals

**In scope:** a static, front-end-only React + TypeScript prototype rendering the 3 rail destinations + all detail/side-panel surfaces above, driven entirely by the seeded mock data. All 10 screens, all component variants, all empty/loading/insufficient_data/boring/error/no-key states.

**Deliberately leave OUT:**
- **No live backend, no API calls, no data fetching** — everything is seeded/local. Show the ops flows (Sync, Run analysis) as simulated progress only.
- **No trade instructions and no price targets, ever.** The default surface is review postures only; trade-ish vocabulary (`STRONG_REVIEW_SELL` etc.) exists solely inside the shadow region, behind the off-by-default toggle, always wrapped in the verbatim unvalidated/educational banner. Introduce no trade or price-target language of your own.
- **No extra colors.** Teal and amber are the only accents; critical uses ink + amber, never red. No decorative hues, no charts/chart-junk, no invented scores.
- **No shadows, no gradients, no elevated cards** (one radial watermark on the setup/empty cover excepted).
- **No auth, no real portfolio persistence, no `eval`/model-bakeoff developer tooling** in the primary UI.
- **Keep it calm.** Home can structurally show at most the answer + a few items; empty/boring/insufficient_data are rendered as first-class honest states with their verbatim strings, not hidden and not alarmed. Silence is the product working. When in doubt, do less.