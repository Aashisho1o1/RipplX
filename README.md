# RipplX

**Evidence-backed company research and filing monitoring for self-directed investors.**

[![Live demo](https://img.shields.io/badge/Live_demo-no_sign--up-2ea44f?style=for-the-badge)](https://ripplx-production-748214fe.up.railway.app/today?demo=1)
[![Open the app](https://img.shields.io/badge/Open_the_app-ripplx-0969da?style=for-the-badge)](https://ripplx-production-748214fe.up.railway.app)

**[See the live demo →](https://ripplx-production-748214fe.up.railway.app/today?demo=1)** — real SEC filings run through the production pipeline, no account needed. [Sign up](https://ripplx-production-748214fe.up.railway.app/signin) to monitor your own companies.

> "I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something
> actually important changed."

Research a company before buying, monitor it automatically, detect downside, receive a weekly
brief, and compare similar companies. Every published filing finding remains tied to exact SEC
evidence, and financial metrics remain deterministic.

RipplX is an educational research tool, not an investment adviser. It never tells a user to buy,
sell, hold, trim, or accumulate a security. The Python package and CLI are named `finwatch`.

## What it does

- **Research a company:** enter a ticker to get a brief with verified financial trends, eight
  downside lenses in one Financial X-Ray, and up to six comparable companies.
- **Analyze the newest filing:** a bounded tool loop publishes zero to three qualitative findings,
  each carrying an exact SEC quotation with accession, section, character offsets, and a link.
- **Monitor:** a scheduled job checks tracked companies, records attention events, and emails
  urgent and weekly summaries.

Numbers appear only in deterministic metric rows computed from SEC XBRL or inside exact SEC
quotations; the model never does arithmetic. Verification proves displayed evidence is exact, not
that the model's interpretation is right, and the UI labels that boundary. See
[`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) for the pipeline, verification, and auth details.

## Zero-key demo

Runs the real pipeline with recorded model output, no network, and no API key:

```bash
uv sync
uv run finwatch demo
```

## Run locally

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and Node.js 22.

```bash
uv sync --extra web
npm --prefix web ci
npm --prefix web run build
uv run finwatch serve
```

Open `http://127.0.0.1:8765`. The first-run screen asks for the SEC User-Agent identity EDGAR
requires. For frontend development, run `npm run dev` in `web/` while `finwatch serve` provides
the API.

Copy `.env.example` to `.env` to configure it. The key values:

- `SEC_USER_AGENT`: your EDGAR contact, e.g. `Your Name your-email@example.com`.
- `FINWATCH_MODEL`: one model with an `openai/`, `openrouter/`, or `z-ai/` prefix (default
  `z-ai/glm-5.2`), plus the matching `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, or `ZAI_API_KEY`.
  The key stays server-side and is never shown to users.
- Stripe and PostHog values are optional. See [`PROVIDERS.md`](PROVIDERS.md).

## Deploy (Railway)

Docker is the supported hosting path. The image builds the frontend, serves UI and API from one
process as a non-root user, and stores SQLite at `/data/finwatch.db`. Hosted signup is public:
visitors sign in with a six-digit email code.

Set these service variables:

```dotenv
SEC_USER_AGENT=Your Name your-email@example.com
FINWATCH_MODEL=z-ai/glm-5.2
ZAI_API_KEY=
FINWATCH_AUTH_SECRET=   # python -c "import secrets; print(secrets.token_urlsafe(32))"
FINWATCH_ALLOWED_HOSTS=your-app.up.railway.app,healthcheck.railway.app
RESEND_API_KEY=
FINWATCH_EMAIL_FROM=RipplX <login@your-verified-domain.example>
RAILWAY_RUN_UID=0
```

Then:

- Mount a persistent volume at `/data`. `RAILWAY_RUN_UID=0` is needed because Railway mounts
  volumes as root, so the non-root user can't otherwise write SQLite.
- Set the healthcheck path to `/healthz`. Railway's probe sends `Host: healthcheck.railway.app`,
  which is why it's in the allowlist.
- Keep one replica. Jobs and login codes live in process memory and SQLite is single-node.
- Verify your sender domain with Resend, or login codes won't send.

The server refuses to start in remote mode if the signing secret, email config, SEC contact, or
host allowlist is missing. Each workspace is capped at 25 tracked tickers and ten model-backed jobs
per hour.

Elsewhere, build and run the same image with `docker build -t finwatch-alpha .` and
`docker run --env-file .env -p 8765:8765 -v finwatch-data:/data finwatch-alpha`, behind TLS.

## CLI

The browser is the product; the CLI is for operators and development. Run
`uv run finwatch --help` for the full list.

```bash
uv run finwatch add AAPL
uv run finwatch ingest
uv run finwatch analyze AAPL
uv run finwatch metrics AAPL          # deterministic, no model key needed
uv run finwatch monitor --weekly
uv run finwatch refresh-showcase      # refresh the public demo (default AAPL, MSFT, NVDA)
```

## Development

```bash
uv sync --frozen --extra web
uv run ruff check .
uv run pytest -q
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
```

Tests make no live network or LLM calls by default.

## License

Apache-2.0. See [`LICENSE`](LICENSE).

> Educational analysis of public information for the portfolio owner's own decision-making. Not
> individualized investment advice. Data may be incomplete or delayed.
