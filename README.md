# finwatch

**Open-source filing intelligence for self-directed investors.**

finwatch watches your holdings, reads new SEC filings, highlights material changes, checks
every number deterministically, and shows *why it matters* — with citations.

> "I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something
> actually important changed."

## 60-second demo (no API key, no setup)

```bash
uv sync
uv run finwatch demo            # runs the full pipeline on bundled filings, prints a digest
uv run finwatch demo --signals  # also show the (unvalidated) shadow-signal block
```

`finwatch demo` runs the **real** pipeline (P0 → P1 → metrics → P2 → verify → P3) over five
bundled SEC filings with a recorded LLM — no network, no keys — and prints a full markdown
digest in under a second. A committed copy is at [`docs/sample_digest.md`](docs/sample_digest.md).
It covers every section: a going-concern **critical red flag** with a claim-backed EDGAR quote,
a watch-only non-reliance filing, a **verified-numbers** table (each value computed
deterministically from XBRL facts, formula-versioned, and traceable — never from the LLM), a
broken thesis, and — behind `--signals` — the shadow signal engine.

## RipplX local web app

RipplX is the local, browser-based interface over the same finwatch trust layer. It uses the
real SQLite repository, deterministic metrics, verifier, bundled demo, and analysis pipeline;
the browser never recreates financial logic or parses the Markdown digest.

```bash
uv sync --extra web
cd web && npm install && npm run build && cd ..
uv run finwatch serve
```

Open `http://127.0.0.1:8765`. First run asks for the SEC User-Agent identity, but the bundled
sample brief is always available without keys or network access. Provider API keys entered in
Settings stay only in the running Python process and are never written to SQLite or returned by
the API. Persistent credentials can still be supplied through the provider environment variables
supported by LiteLLM.

For frontend development, run `npm run dev` from `web/` while `finwatch serve` provides the API;
Vite proxies `/api` to port 8765.

## Verify the backend on real data — no API key

`finwatch demo` proves the wiring on bundled data. To watch the **trust layer run on a live
company** — real SEC XBRL in, deterministic verified numbers out — with **no LLM key**:

```bash
uv run finwatch init
uv run finwatch metrics AAPL          # revenue growth · net income · cash flow · liquidity · leverage
uv run finwatch metrics AAPL --all    # the full engine: Altman-Z, Piotroski-F, valuation percentiles, …
uv run finwatch metrics JPM --all     # a bank — watch the sector-aware not_applicable paths
```

`metrics` needs only `SEC_USER_AGENT`. It fetches the company's filings + XBRL from EDGAR
(adding it as a watch entry if untracked), runs the metrics engine, and prints the numbers —
all deterministic Python; the LLM is never involved. It also persists to `computations`, so a
later `finwatch digest` shows the same verified-numbers table.

You can also assert the live EDGAR path directly, still key-free:

```bash
SEC_USER_AGENT="Your Name you@example.com" uv run pytest -m live \
  tests/test_ingest_live.py tests/test_preprocess_live.py
```

Running the **LLM stages** (P1/P2/P3 → red-flag analysis, thesis checks, the full digest) needs
a model + key — see the Quickstart below. It is cheap: ~1–3 model calls per filing; cap a probe
with `finwatch process --ticker AAPL --limit 1`.

## Status

**v0.2 backend complete.** Built backend-first, phase by phase. See
[`CLAUDE.md`](CLAUDE.md) for the full build specification and
[`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) for the module map.

- [x] **Phase 0** — Scaffold (uv project, CLI skeleton, config, CI, license). Tier 1 trust
      layer transcribed and green.
- [x] **Phase 1** — Data layer + EDGAR ingestion (SQLite schema + migrations + repository;
      SEC-etiquette EDGAR client; ticker→CIK; Stooq prices; `add`/`watch`/`ingest`).
- [x] **Phase 2** — P0 filing preprocessor (form router; canonical sections with offsets,
      element ids, hashes; 8-K item split + furnished detection; amendment linkage;
      risk-factor differ; FTS-synced persistence). Validated on real AAPL 10-K/10-Q/8-K.
- [x] **Phase 3** — XBRL normalization + metrics engine *(most important)*: sector-aware
      `compute_all` wired to the DB, `computations` persistence, concept-map mirror, and a
      five-company hand-verified suite (MSFT/GOOGL/CAT/JPM + a messy small-cap).
- [x] **Phase 4** — Deterministic verifier *(second most important)*: V1–V5 (Tier 1) plus the
      §14 regeneration policy (blocking FAIL → regenerate ≤2 → else manual-review) and
      `verification_results` persistence. Mutation battery green.
- [x] **Phase 5** — LLM layer + P1/P2 pipeline: litellm router (models from env), versioned
      verbatim prompts, pydantic stage schemas, claim-graph persistence, the
      P0→P1→metrics→P2→verify orchestrator, and the golden-set eval harness + `finwatch eval`
      (recorded run meets the DoD: critical recall 100%, verifier pass). Live bake-off is
      operator-run with keys.
- [x] **Phase 6** — Signal engine + shadow log: the deterministic decision matrix (Tier 1)
      wired into the pipeline via adapters; P3 writes rationale only while the engine decides
      the posture/signal (so V3 re-derivation is always an exact match); one-notch escalation
      toward caution; watch records → `NOT_APPLICABLE_WATCHLIST`; every owned evaluation logged
      to the shadow table; `finwatch shadow report`.
- [x] **Phase 7** — Digest + demo + release polish: deterministic markdown renderer
      (reproducible from the DB, no LLM at render time), `finwatch demo` (zero-key, bundled
      fixtures), `finwatch digest [--since] [--until] [--signals] [--out]`, and this README.
- [x] **Review hardening (post-v0.2)** — an external adversarial review drove fixes across the
      trust layer: the production pipeline is now wired into the CLI (`process`/`analyze`/
      `verify`); the verifier no longer skips V2 accounting identities, re-derives the full P3
      decision (V3), is sign-aware (V1) and catches the `$N target` price form (V5), and now
      scans P3 rationale prose; XBRL is point-in-time (no future-filed facts) with per-accessor
      tag resolution and period-matched valuation history; LLM output contracts (vocabularies +
      claim graph) are enforced by the schemas; and several signal-matrix edge cases were
      corrected. See the `fix:`/`feat:` commits for the finding-by-finding detail.
- [x] **RipplX web prototype** — local FastAPI adapter, structured presentation contracts,
      real bundled-demo projection, session-only LLM credentials, sequential sync/analysis jobs,
      and a lean React/TypeScript interface for the Brief, holdings, evidence, metrics, settings,
      and shadow track record.

## Quickstart (development)

```bash
uv sync                     # create venv, install deps
uv run finwatch --help      # see the CLI surface
uv run pytest               # run the test suite
```

`finwatch` requires a SEC User-Agent for any command that hits EDGAR. Copy `.env.example` to
`.env` and set:

```
SEC_USER_AGENT="Your Name your-email@example.com"
```

The SEC requires this header for all API access; finwatch refuses to make network calls
without it.

Then track holdings, pull their filings, run the analysis pipeline, and read the digest:

```bash
uv run finwatch init                              # create the database
uv run finwatch add AAPL --shares 10 --cost 150   # owned holding (thesis optional)
uv run finwatch watch MSFT                         # track without ownership
uv run finwatch ingest                             # pull filings, XBRL facts, EOD prices
uv run finwatch process                            # run P0→P1→metrics→P2→verify→P3, persist analyses
uv run finwatch digest                             # render the markdown digest from the DB
```

`process` (and `analyze TICKER`) run the LLM stages, so set the model strings in `.env`
(`FINWATCH_MODEL_EXTRACT`, `FINWATCH_MODEL_REASON` — any litellm model string). `ingest` and
`digest` need no model; `finwatch demo` needs no config at all. `finwatch verify ACCESSION`
re-runs the deterministic verifier on a stored analysis offline.

> **Dev note:** if `uv run finwatch` can't import the package (a uv/`site` editable-install
> quirk on some Python builds), reinstall it non-editable: `uv sync --no-editable`. Tests are
> unaffected — they run against `src/` directly.

## What this is — and is NOT

finwatch is an **open-source research tool**. It does **not** provide investment advice.

- The LLM never performs arithmetic and never sources a number from its own weights. Numbers
  enter only from (a) SEC XBRL structured data or (b) verbatim extraction with full
  provenance. All computation happens in deterministic Python, and a deterministic verifier
  is the compile pass: analyses that fail it do not ship.
- The default digest ships **review postures** (critical_review / risk_review / monitor /
  positive_support / insufficient_data), never trade instructions.
- A signal engine exists but runs in **shadow mode** — its hypothetical signals are
  experimental, unvalidated output, logged to build an auditable track record. They are
  visible only behind an explicit `--signals` flag and are OFF by default.
- **You are responsible for your own decisions.**

### Shadow-signal promotion policy

Shadow signals are trade-action *vocabulary* (e.g. `STRONG_REVIEW_SELL`) evaluated by the
deterministic matrix and logged to `signal_shadow_log` on every owned evaluation — with the
rules that fired, the rules skipped (and why), the computed inputs, and the EOD price at
evaluation. They are **never** shown in the default digest. A hypothetical signal may become
default-visible only after **all** of the following hold:

1. **≥ 100 logged shadow evaluations**, and
2. a **human audit of ≥ 20 sampled cases**, and
3. the acceptance gates below pass.

Until then, the product ships **review postures**, and `finwatch shadow report` /
`finwatch digest --signals` surface the shadow record clearly labelled *unvalidated,
educational*. Track record ≠ endorsement.

## Acceptance gates (v0.2 release checklist)

1. Zero V1 numeric orphans across real filings — every rendered number traces to a computation,
   an XBRL fact, or a verbatim evidence snippet.
2. 100% recall on critical golden-set items (a missed going-concern is disqualifying); ≥ 90% on high.
3. 100% V3 agreement between each P3 output and a fresh matrix re-derivation.
4. Boring-filing silence — routine filings collapse to a single line, never an alert.
5. `finwatch demo` works on a fresh clone with no keys, in under 60 seconds.
6. A 10-ticker weekly digest completes in minutes and well under $0.10 at bake-off pricing.
7. The shadow log is populated for every evaluated filing; `--signals` output carries the
   unvalidated-shadow label.

## License

Apache-2.0. See [`LICENSE`](LICENSE). Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied.
