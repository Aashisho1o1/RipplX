# finwatch

**Open-source filing intelligence for self-directed investors.**

finwatch watches your holdings, reads new SEC filings, highlights material changes, checks
every number deterministically, and shows *why it matters* — with citations.

> "I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something
> actually important changed."

## Status

🚧 **Under construction (v0.2).** Building backend-first, phase by phase. See
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
- [ ] Phase 6 — Signal engine + shadow log
- [ ] Phase 7 — Digest + demo + release polish

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

Then track holdings and ingest their filings + financials:

```bash
uv run finwatch init                              # create the database
uv run finwatch add AAPL --shares 10 --cost 150   # owned holding (thesis optional)
uv run finwatch watch MSFT                         # track without ownership
uv run finwatch ingest                             # pull filings, XBRL facts, EOD prices
```

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

## License

Apache-2.0. See [`LICENSE`](LICENSE). Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied.
