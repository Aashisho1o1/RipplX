# finwatch

**Evidence-backed company research and filing monitoring for self-directed investors.**

Research a company before buying, monitor it automatically, detect downside, receive a weekly
brief, and compare similar companies. Every published filing finding remains tied to exact SEC
evidence, and financial metrics remain deterministic.

> "I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something
> actually important changed."

finwatch is an educational research tool, not an investment adviser. It never tells a user to
buy, sell, hold, trim, or accumulate a security.

## Launch scope

The current repository contains a lean commercial loop:

1. Enter a ticker. The Research Company action resolves it, syncs filings and SEC companyfacts,
   computes verified metrics, and opens the Before You Buy Brief. When analysis is configured it
   also starts the newest-filing analysis and then connected research in the background. No saved
   watch condition, payment, or brokerage connection is required.
2. Analyze at most one filing per request: the newest supported filing for a selected ticker,
   or the newest supported filing across tracked tickers when no ticker is selected. You can narrow
   the run to the newest 10-K, 10-Q, or 8-K. An already terminal newest filing is a no-op; the
   system never falls through to older filings within the selected scope.
3. Research the filing through a bounded allowlisted tool loop, then publish zero to three
   qualitative findings. Every finding must carry an exact quotation with accession, section,
   server-derived character offsets, section hash, and an HTTPS SEC link.
4. Show only the starter metrics: revenue growth, net-income trend, operating cash flow,
   liquidity, share-count change, and a net-debt / (operating income + D&A) leverage proxy.
   Share-count direction is reported neutrally—not inferred to be a buyback or dilution. Stale,
   future-dated, or malformed source periods are shown as unavailable, never relabeled as current.
5. Combine eight reproducible downside lenses and the six verified metrics in one Financial X-Ray.
   Missing data stays `unavailable`.
6. Monitor supported filings with one idempotent scheduled command, persist attention events, and
   deliver urgent/same-week and weekly email summaries through Resend.
7. Build a deterministic Stock Impact Snapshot from verified findings, risk lenses, and the user's
   saved valuation. Show trailing P/E, P/FCF, FCF yield, and scenario percentage changes downstream
   of the fixed starter metrics.
8. Let users save up to five watch conditions, ask bounded evidence-grounded questions, and compare
   user-editable SIC-derived peer candidates. Filing commitments remain optional supporting context.

Numbers may appear only in deterministic metric rows sourced from SEC XBRL or inside exact SEC
quotations. Structured direction claims are compiled against current-minus-prior metric deltas and
SEC-decimals rounding slack. The browser and Markdown digest use the same canonical presentation
model. Finding-local failures drop only that finding; surviving findings and metrics publish.
Provider/malformed-action breakdown and filing-scope/critical-coverage failures withhold the run.
After a compiler-passing baseline exists, an optional Skeptic or repair protocol/budget failure
preserves unobjected findings and deterministically drops only findings carrying validated
objections. Change-span validity is precomputed and never depends on model tool-call order.

The model still makes the qualitative selection, headline, and importance judgment. Verification
proves that its displayed evidence is exact and that displayed numbers come from allowed sources;
it does not prove semantic entailment or make the model's interpretation deterministic. Both
renderers label that boundary explicitly, and the concierge alpha manually reviews every result.

The launch path still does **not** execute or expose:

- P2 portfolio-impact or cross-holding analysis;
- P3 signals, trade-action vocabulary, shadow logs, promotion policy, or track-record UI;
- offline reverify or historical analysis replay;
- portfolio accounting, position sizing, rebalancing, or extended forensic-score suites;
- personalized buy/hold/sell/trim instructions, price targets, or trading; or
- open-ended provider/model routing (only the `openai/`, `openrouter/`, and `z-ai/` prefixes are
  accepted, each mapping to one fixed endpoint).

Dormant research modules and historical tests may remain in the repository to preserve prior
work, but the launch assembly does not construct, execute, render, or advertise them.

## Zero-key demo

The bundled demo runs the real launch pipeline with recorded model output, no network, and no
API key:

```bash
uv sync
uv run finwatch demo
```

It exercises deterministic preprocessing, evidence-backed extraction, the starter metrics,
verification, and the same canonical presentation DTO consumed by the browser and Markdown
renderer.

## Public SEC showcase

Hosted visitors can inspect a read-only SEC showcase before signing in. Normal page loads read only
cached database artifacts and never contact EDGAR or an LLM. An operator refreshes the curated set
through the existing ingestion, metrics, analysis, and verification path:

```bash
uv run finwatch refresh-showcase
# or choose one to five issuers
uv run finwatch refresh-showcase --ticker AAPL --ticker MSFT
```

The default set is AAPL, MSFT, and NVDA. Publication is atomic: the old showcase remains visible
unless every selected issuer has a completed newest supported filing. If no successful refresh has
been published, the public routes automatically use the bundled zero-key SEC fixtures. The operator
refresh requires `SEC_USER_AGENT`, `FINWATCH_MODEL`, and that model's server-side credential; none
of those values are requested from or exposed to visitors.

This prototype deliberately has no market-price provider. SEC filings and companyfacts supply the
financial source data; valuation remains explicit and user-directed by entering a price to evaluate.
The valuation run records its date and assumptions rather than labeling that input as a live quote.

## Run locally

Requirements: Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and Node.js 22 for building
the browser assets.

```bash
uv sync --extra web
npm --prefix web ci
npm --prefix web run build
uv run finwatch serve
```

Open `http://127.0.0.1:8765`. Loopback is the default and local mode does not require an access
token. The first-run screen asks for the SEC User-Agent identity required by EDGAR.

For frontend development, run `npm run dev` from `web/` while `finwatch serve` provides the API;
Vite proxies `/api` to port 8765.

On Windows, `scripts\start_demo.cmd` starts the built local app and backs up an existing
`data\finwatch.db` before launch. Pass `-SkipBackup` only when that safety copy is unnecessary.

### Configuration

Copy `.env.example` to `.env`. The real process environment takes precedence over `.env`.

```dotenv
SEC_USER_AGENT=Your Name your-email@example.com
FINWATCH_DB=./data/finwatch.db
FINWATCH_MODEL=z-ai/glm-5.2
FINWATCH_SKEPTIC_MODEL=
ZAI_API_KEY=
OPENROUTER_API_KEY=
OPENAI_API_KEY=
STRIPE_SECRET_KEY=
STRIPE_PRICE_ID=
STRIPE_WEBHOOK_SECRET=
POSTHOG_PROJECT_KEY=
POSTHOG_HOST=https://us.i.posthog.com
```

- `SEC_USER_AGENT` identifies the EDGAR client. The local browser can also collect it during
  setup; unlike an API key, this setting is persisted in SQLite.
- `FINWATCH_MODEL` is the single operator-selected launch model and must use the `openai/`,
  `openrouter/`, or `z-ai/` LiteLLM prefix. Each maps to exactly one fixed endpoint; `z-ai/<model>`
  reaches Zhipu GLM through z.ai's OpenAI-compatible coding endpoint. The browser displays it
  read-only.
- `FINWATCH_SKEPTIC_MODEL` optionally selects a stronger finance Skeptic on the same provider. When
  absent, the Generator model is reused. The Skeptic can object to a finding but cannot approve it.
- `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, or `ZAI_API_KEY` (matching the model prefix) is the
  production provider credential read from the environment. A key for the wrong provider is
  reported as not configured rather than failing mid-run.
- RipplX supplies this credential server-side. The browser never asks users for a provider key,
  and the API reports only whether analysis is available.
- Stripe owns card data; RipplX stores only customer/subscription identifiers and status. PostHog is
  optional, server-side, allowlisted, and receives no tickers, holdings, thesis text, valuation
  inputs, filing text, or financial values. See `PROVIDERS.md`.

Do not commit `.env`; it is ignored by Git. The demo needs none of these values.

### Browser workflow

1. Add one or more tickers under **Tracked tickers**.
2. Run **Sync filings** to index SEC filings and ingest companyfacts.
3. Run **Analyze a filing**, choose Latest, 10-K, 10-Q, or 8-K, and start the run. Each request
   processes at most one newest filing in that scope.
4. Read the findings and click the SEC evidence links. A routine filing may correctly produce no
   findings.

## Hosted alpha: public email-code access

Docker is the only supported hosted-alpha packaging path. The image builds the React frontend,
installs the Python application from `uv.lock`, serves UI and API from one process, runs as a
non-root user, and stores SQLite at `/data/finwatch.db`.

Hosted signup is public: a visitor enters any valid email address, receives a six-digit code, and
gets a private workspace. There is no invite list or password. Login codes live for ten minutes in
the one server process; the signed login cookie lasts 30 days. Watchlists, preferences, and jobs are
user-scoped. The operator-managed provider key never reaches the browser. Public SEC filings, XBRL
facts, and verified analyses are reused across workspaces.

Create a `.env` file on the deployment host:

```dotenv
SEC_USER_AGENT=Your Name your-email@example.com
FINWATCH_MODEL=z-ai/glm-5.2
ZAI_API_KEY=
FINWATCH_AUTH_SECRET=replace-with-a-random-value-of-at-least-32-characters
FINWATCH_ALLOWED_HOSTS=alpha.example.com
RESEND_API_KEY=re_your_resend_key
FINWATCH_EMAIL_FROM=RipplX <login@your-verified-domain.example>
```

On Railway, set the same six values in the service Variables tab, mount a persistent volume at
`/data`, and set `FINWATCH_ALLOWED_HOSTS` to the exact Railway/custom-domain hostname. Set the
provider key matching `FINWATCH_MODEL` (`ZAI_API_KEY`, `OPENAI_API_KEY`, or `OPENROUTER_API_KEY`) so
hosted onboarding never demands a key from a participant; that key stays in process environment
memory and is never returned by the API.

Generate the cookie-signing secret without placing it in shell history:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Build and run the one supported image:

```bash
docker build -t finwatch-alpha .
docker run --rm \
  --env-file .env \
  -p 8765:8765 \
  -v finwatch-data:/data \
  finwatch-alpha
```

Required remote controls:

- `FINWATCH_AUTH_SECRET` must contain at least 32 characters. Rotating it signs everyone out.
- `RESEND_API_KEY` and `FINWATCH_EMAIL_FROM` send login codes. Verify the sender domain with Resend
  before launch. Provider failures are returned to users as fixed, non-diagnostic messages.
- `FINWATCH_ALLOWED_HOSTS` must contain the exact public hostname, without scheme or path. Use a
  comma-separated list only when the same instance genuinely has multiple trusted hostnames.
- `SEC_USER_AGENT` is the operator's EDGAR contact and is never exposed as the participant email.
- Terminate TLS in front of the container. Hosted cookies are `Secure`, and the provider key must not
  cross a plaintext public connection.
- Keep the service at one instance. Jobs live in process memory and are lost on restart; SQLite
  is a single-node store. Stop the instance before a raw filesystem copy/snapshot of `/data`, test
  restores, and apply the host's encryption and access controls.
- Each workspace is capped at 25 tracked tickers. The process still runs one job globally at a time.
- `GET /healthz` is intentionally public and returns only service health. Interactive API docs
  are disabled in remote mode.
- Sessions are stateless. Logout removes this browser's cookie, but a
  copied cookie is not centrally revocable; it remains valid until its expiry or signing-secret
  rotation. This is an accepted public-alpha limitation with no persistent session registry.

The operator key stays in process environment memory. The API reports only whether analysis is
configured, never the credential itself, and the browser never displays a provider-key field.

Schema v8 is a clean prototype break, not a migration. It retains attempt-linked `harness.v2` and
frozen `certificate.v2` semantics and adds private research/monitoring state plus owner-scoped
`company_research.v1` reports. Before upgrading an existing Railway volume,
back up `/data`, stop the old deployment, recreate the database/volume, and deploy. Old schemas fail
with an explicit backup-and-reset error. `.env` files are excluded from both Git and the Docker build
context; keep a local copy mode-restricted (for example `chmod 600 .env`).

Remote serving fails closed if its signing secret, email sender configuration, SEC contact, or host
allowlist is missing. The CLI also refuses a non-loopback bind unless `--allow-remote` is explicit.

## CLI and developer tooling

The browser is the launch product. The CLI remains useful for operators and development:

```bash
uv run finwatch init
uv run finwatch add AAPL
uv run finwatch ingest
uv run finwatch analyze AAPL
uv run finwatch digest
uv run finwatch monitor
uv run finwatch monitor --weekly
uv run finwatch metrics AAPL
```

`metrics` is deterministic and needs no model key. `eval` remains developer-only bake-off tooling
and is not part of the production model-routing surface. Run `uv run finwatch --help` for the
authoritative command list.

## Trust and data handling

- Filing text is untrusted input. It is isolated as data in the extraction prompt, and only
  exact, deterministically rechecked evidence reaches the launch DTO.
- The LLM never performs arithmetic or supplies a numeric conclusion from model memory.
- Starter metrics are deterministic Python computations over point-in-time SEC XBRL facts.
- Annual metric sources older than 550 days and instant/share sources older than 200 days fail
  closed as unavailable. Non-finite, future, missing-date, and malformed facts are rejected.
- Direction deltas and rounding slacks are combined with decimal arithmetic; unrepresentable finite
  SEC decimal exponents remain unknown rather than becoming false zero uncertainty.
- Findings are capped at three and must be qualitative; numbers belong in exact evidence.
- One shared authored-headline policy gates quantities, trade instructions, price targets,
  first-person valuation, and forbidden vocabulary in the compiler, V5, and final DTO check.
- The deterministic compiler never edits failed content into compliance. After one shared repair,
  finding-local errors are pruned with typed reason codes. Whole-run withholding is reserved for
  provider/action breakdown or filing-scope/critical-coverage failure.
- Retries are counted from `download`, including missing primary-document URLs and fetch failures;
  after two failed attempts that issuer no longer blocks another tracked issuer's eligible filing.
- Each completed verified or completed-withheld filing exposes a compact finalized tool trace and
  an owner-scoped attempt-bound `certificate.v2`. Withheld certificates are redacted before hashing;
  pending, failed, malformed, mismatched, and v1 attempts expose no certificate. Raw model output,
  secrets, and provider exceptions are not exposed.
- React renders filing/model text as escaped text; the launch UI does not render raw filing HTML.
- SQLite and the `/data` volume are plaintext unless the operator supplies filesystem or volume
  encryption. They contain account emails, private ticker membership/preferences, SEC data,
  generated analyses, and the SEC User-Agent. They do not contain login codes, session cookies, or
  provider keys. Schema upgrades require a fresh database rather than carrying legacy
  prototype fields forward.

The disclaimer remains part of every canonical digest:

> Educational analysis of public information for the portfolio owner's own decision-making. Not
> individualized investment advice. Data may be incomplete or delayed.

## Early-user feedback

The first launch is deliberately supervised. See
[`docs/CONCIERGE_ALPHA.md`](docs/CONCIERGE_ALPHA.md) for the early-user protocol, manual digest
review checklist, and feedback questions. Signup itself is public; recruitment and feedback remain
operator-led.

## Development checks

```bash
uv sync --frozen --extra web
uv run ruff check .
uv run pytest -q
npm --prefix web ci
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run build
```

Tests make no live network or LLM calls by default. Optional live checks are marked `live` and
excluded from the normal suite.

The broader v0.2 research system (P2/P3, signals, extended metrics) was removed in the lean cut;
recover it from Git history if a future product decision justifies it.

The pinned next iteration is intentionally narrow: when a registered metric is unavailable only
because one fact is missing, `resolve_fact` may attempt bounded Tier-0→Tier-1 recovery from the
current SEC filing text. Company IR, news, market data, generic plugins, subagents, Lean/Z3, and
distributed job infrastructure remain deferred.

## License

Apache-2.0. See [`LICENSE`](LICENSE). Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied.
