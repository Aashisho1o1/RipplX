# finwatch

**Evidence-backed SEC filing alerts for self-directed investors.**

Add the tickers you follow. When a 10-K, 10-Q, or 8-K arrives, finwatch shows up to three
important changes, the exact SEC evidence behind each change, and a small set of
deterministically computed financial deltas.

> "I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something
> actually important changed."

finwatch is an educational research tool, not an investment adviser. It never tells a user to
buy, sell, hold, trim, or accumulate a security.

## Launch scope

The current repository contains the launch cut of a larger research prototype. The user-facing
loop is intentionally narrow:

1. Track a ticker. No shares, cost basis, target weight, investment horizon, or thesis is
   collected during onboarding.
2. Sync filings and SEC companyfacts from EDGAR.
3. Analyze at most one filing per request: the newest supported filing for a selected ticker,
   or the newest supported filing across tracked tickers when no ticker is selected. You can narrow
   the run to the newest 10-K, 10-Q, or 8-K. An already terminal newest filing is a no-op; the
   system never falls through to older filings within the selected scope.
4. Produce zero to three qualitative findings. Every finding must carry an exact quotation with
   accession, section, character offsets, section hash, and an HTTPS SEC link.
5. Show only the starter metrics: revenue growth, net-income trend, operating cash flow,
   liquidity, share-count change, and a net-debt / (operating income + D&A) leverage proxy.
   Share-count direction is reported neutrally—not inferred to be a buyback or dilution. Stale,
   future-dated, or malformed source periods are shown as unavailable, never relabeled as current.

Numbers may appear only in deterministic metric rows sourced from SEC XBRL or inside exact SEC
quotations. The browser and Markdown digest use the same canonical presentation model. If a
blocking verifier or presentation-integrity check fails, all LLM-derived findings are withheld;
the user sees a manual-review state instead of partial analysis.

The model still makes the qualitative selection, headline, and importance judgment. Verification
proves that its displayed evidence is exact and that displayed numbers come from allowed sources;
it does not prove semantic entailment or make the model's interpretation deterministic. Both
renderers label that boundary explicitly, and the concierge alpha manually reviews every result.

The launch path does **not** execute or expose:

- P2 portfolio-impact or cross-holding analysis;
- P3 signals, trade-action vocabulary, shadow logs, promotion policy, or track-record UI;
- offline reverify or historical analysis replay;
- portfolio accounting, position sizing, rebalancing, thesis checks, or extended valuation and
  forensic-score suites; or
- open-ended provider/model routing (only the `openai/` and `openrouter/` prefixes are accepted).

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
FINWATCH_MODEL=openai/your-evaluated-model
OPENAI_API_KEY=
```

- `SEC_USER_AGENT` identifies the EDGAR client. The local browser can also collect it during
  setup; unlike an API key, this setting is persisted in SQLite.
- `FINWATCH_MODEL` is the single operator-selected launch model and must use the `openai/` or
  `openrouter/` LiteLLM prefix. The browser displays it read-only.
- `OPENAI_API_KEY` or `OPENROUTER_API_KEY` (matching the model prefix) is the production provider
  credential read from the environment.
- Instead of setting `OPENAI_API_KEY`, a local user may enter a key in Settings. That key exists
  only in the running Python process, is never written to SQLite or returned by the API, and is
  lost on restart. `FINWATCH_MODEL` must still be configured by the operator.

Do not commit `.env`; it is ignored by Git. The demo needs none of these values.

### Browser workflow

1. Add one or more tickers under **Tracked tickers**.
2. Run **Sync filings** to index SEC filings and ingest companyfacts.
3. Run **Analyze a filing**, choose Latest, 10-K, 10-Q, or 8-K, and start the run. Each request
   processes at most one newest filing in that scope.
4. Read the findings and click the SEC evidence links. A routine filing may correctly produce no
   findings.

## Hosted alpha: one Docker deployment path

Docker is the only supported hosted-alpha packaging path. The image builds the React frontend,
installs the Python application from `uv.lock`, serves UI and API from one process, runs as a
non-root user, and stores SQLite at `/data/finwatch.db`.

This is a single-operator alpha, not a public SaaS deployment. Its bearer token is an
**operator/admin credential**, not a participant login and not tenant authorization. Never give one
instance or token to multiple concierge participants. Either keep the experience operator-mediated
(the operator alone accesses finwatch and shares only a manually reviewed digest), or provision one
isolated container, SQLite database/volume, hostname, and bearer token per participant. Each
container still runs one in-process worker and uses one persistent volume at `/data`. Hosted ticker
registration is serialized and capped at 25 tracked tickers per workspace; that is a resource and
LLM-wallet bound, not tenant isolation.

Create a `.env` file on the deployment host:

```dotenv
SEC_USER_AGENT=Your Name your-email@example.com
FINWATCH_MODEL=openai/your-evaluated-model
OPENAI_API_KEY=your-openai-key
FINWATCH_AUTH_TOKEN=replace-with-a-random-value-of-at-least-32-characters
FINWATCH_ALLOWED_HOSTS=alpha.example.com
```

Generate an access token without placing it in shell history:

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

- `FINWATCH_AUTH_TOKEN` must contain at least 32 characters. It is the operator/admin credential;
  never reuse or distribute it as a participant password. Every `/api/*` request requires it as an
  `Authorization: Bearer` token. The unlock screen keeps it only in JavaScript module memory; it is
  not written to browser storage and is intentionally lost on refresh.
- `FINWATCH_ALLOWED_HOSTS` must contain the exact public hostname, without scheme or path. Use a
  comma-separated list only when the same instance genuinely has multiple trusted hostnames.
- Terminate TLS in front of the container. Never send the bearer token or an API key over a
  plaintext public connection.
- Keep the service at one instance. Jobs live in process memory and are lost on restart; SQLite
  is a single-node store. Stop the instance before a raw filesystem copy/snapshot of `/data`, test
  restores, and apply the host's encryption and access controls.
- Keep each workspace at or below 25 tracked tickers. Registration is serialized to enforce the
  cap, but the cap does not create participant accounts or authorization boundaries.
- `GET /healthz` is intentionally public and returns only service health. Interactive API docs
  are disabled in remote mode.

Remote serving fails closed if the auth token or host allowlist is missing. The CLI also refuses
a non-loopback bind unless `--allow-remote` is explicit. The token provides an operator boundary
only: it is not user accounts, tenant isolation, or authorization between participants. Concierge
participants must never share direct access to an instance; use operator-mediated review or one
isolated DB/container/token deployment per participant.

## CLI and developer tooling

The browser is the launch product. The CLI remains useful for operators and development:

```bash
uv run finwatch init
uv run finwatch add AAPL
uv run finwatch ingest
uv run finwatch analyze AAPL
uv run finwatch digest
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
- Findings are capped at three and must be qualitative; numbers belong in exact evidence.
- The deterministic verifier never edits failed content into compliance. Blocking failure means
  withholding.
- React renders filing/model text as escaped text; the launch UI does not render raw filing HTML.
- SQLite and the `/data` volume are plaintext unless the operator supplies filesystem or volume
  encryption. They contain tracked tickers, SEC data, generated analyses, and the SEC User-Agent.
  They do not contain OpenAI keys or the hosted bearer token. A database upgraded from the broader
  research prototype may still contain dormant legacy portfolio fields; audit or start with a fresh
  alpha database before inviting participants.

The disclaimer remains part of every canonical digest:

> Educational analysis of public information for the portfolio owner's own decision-making. Not
> individualized investment advice. Data may be incomplete or delayed.

## Concierge alpha

The first launch is deliberately supervised. See
[`docs/CONCIERGE_ALPHA.md`](docs/CONCIERGE_ALPHA.md) for the 5–10-user protocol, manual digest
review checklist, and the seven feedback questions. The application does not recruit or contact
participants automatically.

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

## License

Apache-2.0. See [`LICENSE`](LICENSE). Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied.
