"""finwatch command-line interface (Typer).

Phase 0 provides the full command skeleton mirroring CLAUDE.md §5. Each command is
stubbed and marks the phase that will implement it. ``finwatch init`` already wires
the config hard-fail so the SEC_USER_AGENT requirement is live and testable.
"""

from __future__ import annotations

import os
import sys

import typer
from rich.console import Console

from finwatch import __version__
from finwatch.config import Config, ConfigError, load_config, load_dotenv
from finwatch.ingest import (
    DEFAULT_BACKFILL_QUARTERS,
    TickerNotFoundError,
    build_service,
)

if os.name == "nt":
    # Windows may default redirected/legacy console streams to CP-1252. The digest
    # intentionally contains Unicode symbols (for example arrows and checkmarks),
    # so make the documented CLI demo reliable without requiring `chcp 65001`.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except OSError:
                pass

console = Console()


def _config_or_exit() -> Config:
    try:
        return load_config()
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(code=1) from exc


app = typer.Typer(
    name="finwatch",
    help="Open-source filing intelligence for self-directed investors.",
    no_args_is_help=True,
    add_completion=False,
)
def _version_callback(value: bool) -> None:
    if value:
        console.print(f"finwatch {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """finwatch — watch your holdings' SEC filings; know when something important changed."""


@app.command()
def init() -> None:
    """Create the database + folders and verify SEC_USER_AGENT is set."""
    from finwatch.db import init_db

    cfg = _config_or_exit()
    conn = init_db(cfg.db_path)  # creates parent dirs + applies schema
    conn.close()
    console.print(f"[green]✓[/] SEC_USER_AGENT configured. Initialized database at {cfg.db_path}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address (loopback by default)."),
    port: int = typer.Option(8765, min=1, max=65535, help="HTTP port."),
    allow_remote: bool = typer.Option(
        False, "--allow-remote", help="Explicitly allow a non-loopback bind address."
    ),
) -> None:
    """Serve the local RipplX web application and API."""
    # Make .env (FINWATCH_MODEL, OPENROUTER_API_KEY, SEC_USER_AGENT) available to the
    # web app. Real environment variables still win (load_dotenv uses setdefault), and
    # no key is required — the demo brief and verified numbers work without one.
    load_dotenv()
    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
        console.print(
            "[yellow]Refusing a non-loopback bind.[/] Pass [bold]--allow-remote[/] "
            "only for an authenticated hosted alpha."
        )
        raise typer.Exit(code=1)
    try:
        import uvicorn

        from finwatch.web import create_app
    except (ImportError, RuntimeError) as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(code=1) from exc
    remote = host not in {"127.0.0.1", "localhost", "::1"}
    try:
        web_app = create_app(remote=remote)
    except RuntimeError as exc:
        console.print(f"[red]Web security configuration error:[/] {exc}")
        raise typer.Exit(code=1) from exc
    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command()
def add(
    ticker: str = typer.Argument(..., help="Ticker to track."),
) -> None:
    """Track one ticker; no portfolio accounting data is collected."""
    cfg = _config_or_exit()
    conn, service = build_service(cfg)
    try:
        company = service.track_company(ticker)
    except TickerNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    finally:
        service.edgar.close()
        conn.close()
    console.print(
        f"[green]✓[/] Tracking [bold]{company.ticker}[/] (CIK {company.cik}). "
        f"Run [bold]finwatch ingest[/] to pull filings and XBRL facts."
    )


def _require_model(cfg: Config) -> None:
    if not cfg.model:
        console.print(
            "[red]LLM model not configured.[/] Set [bold]FINWATCH_MODEL[/] to one "
            "OpenAI-backed litellm model string in your .env to run the "
            "analysis pipeline. No key is needed for [bold]finwatch demo[/]."
        )
        raise typer.Exit(code=1)


def _run_pipeline(cfg: Config, *, cik: str | None):
    """Build the production orchestrator and process only the newest filing in scope."""
    from pathlib import Path

    from finwatch.db import Repo, init_db
    from finwatch.ingest import EdgarClient
    from finwatch.llm.router import LiteLLMClient
    from finwatch.pipeline.run import build_orchestrator, process_latest

    conn = init_db(cfg.db_path)
    repo = Repo(conn)
    cache_dir = Path(cfg.db_path).parent / "cache" if cfg.db_path != ":memory:" else None
    edgar = EdgarClient(cfg.sec_user_agent, cache_dir=cache_dir)
    llm = LiteLLMClient(cfg.model)
    skeptic = LiteLLMClient(cfg.skeptic_model) if cfg.skeptic_model else llm
    orch = build_orchestrator(
        repo,
        llm=llm,
        skeptic_llm=skeptic,
        companyfacts_provider=lambda c: edgar.companyfacts(c),
        model=cfg.model,
        skeptic_model=cfg.skeptic_model or cfg.model,
    )

    def fetch_html(url: str) -> str:
        return edgar.fetch_primary_doc(url).decode("utf-8", "replace")

    try:
        return process_latest(repo, orch, fetch_html, cik=cik)
    finally:
        edgar.close()
        conn.close()


def _print_pipeline_results(results) -> None:
    for r in results:
        if r.ok:
            mark = "[yellow]⚠ withheld[/]" if r.withheld else f"[green]{r.verdict}[/]"
            console.print(f"[green]✓[/] {r.ticker} {r.accession} — {mark}")
        else:
            console.print(f"[yellow]![/] {r.ticker} {r.accession} — [red]{r.error}[/]")
    ok = sum(1 for r in results if r.ok)
    console.print(
        f"[bold]Processed {ok}/{len(results)} filing(s).[/] "
        f"Run [bold]finwatch digest[/] to see the report."
    )


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Ticker to analyze (must be tracked + ingested)."),
) -> None:
    """Analyze the newest supported filing for a tracked ticker; never backfill history."""
    from finwatch.db import Repo, init_db

    cfg = _config_or_exit()
    _require_model(cfg)
    conn = init_db(cfg.db_path)
    company = Repo(conn).get_company_by_ticker(ticker)
    conn.close()
    if company is None:
        console.print(
            f"[red]{ticker} is not tracked.[/] Run [bold]finwatch add {ticker}[/] then "
            "[bold]finwatch ingest[/] first."
        )
        raise typer.Exit(code=1)
    results = _run_pipeline(cfg, cik=company.cik)
    if not results:
        console.print(
            f"The newest filing for {ticker} is already terminal. Run [bold]finwatch ingest[/] "
            "to pull a newer filing."
        )
        return
    _print_pipeline_results(results)


@app.command()
def ingest(
    backfill: int | None = typer.Option(
        None, "--backfill", help="Quarters of filing history to index (default 8)."
    ),
) -> None:
    """Pull filings and SEC companyfacts for tracked CIKs."""
    cfg = _config_or_exit()
    conn, service = build_service(cfg)
    quarters = backfill if backfill is not None else DEFAULT_BACKFILL_QUARTERS
    try:
        if not service.repo.list_tracked_ciks():
            console.print(
                "No tracked companies yet. Add one with [bold]finwatch add TICKER[/]."
            )
            return
        summary = service.ingest_all(backfill_quarters=quarters)
    finally:
        service.edgar.close()
        conn.close()

    for r in summary.results:
        if r.error:
            console.print(
                f"[yellow]![/] {r.ticker}: {r.filings_indexed} filings, "
                f"{r.xbrl_facts} facts — [red]{r.error}[/]"
            )
        else:
            console.print(
                f"[green]✓[/] {r.ticker}: {r.filings_indexed} filings "
                f"({r.filings_new} new), {r.xbrl_facts} XBRL facts"
            )
    console.print(
        f"[bold]Ingest complete:[/] {summary.companies} companies, "
        f"{summary.filings} filings, {summary.xbrl_facts} XBRL facts."
    )


@app.command()
def process(
    ticker: str | None = typer.Option(
        None, "--ticker", help="Only process this tracked ticker's filings."
    ),
) -> None:
    """Analyze only the newest supported filing in scope and persist its verified result."""
    from finwatch.db import Repo, init_db

    cfg = _config_or_exit()
    _require_model(cfg)
    cik = None
    if ticker:
        conn = init_db(cfg.db_path)
        company = Repo(conn).get_company_by_ticker(ticker)
        conn.close()
        if company is None:
            console.print(
                f"[red]{ticker} is not tracked.[/] "
                f"[bold]finwatch add {ticker} && finwatch ingest[/] first."
            )
            raise typer.Exit(code=1)
        cik = company.cik
    results = _run_pipeline(cfg, cik=cik)
    if not results:
        console.print("The newest filing is already terminal. Run [bold]finwatch ingest[/].")
        return
    _print_pipeline_results(results)


def _print_metrics_table(ticker: str, as_of: str, rows: list[tuple[str, str, str, str]]) -> None:
    from rich.table import Table

    table = Table(title=f"{ticker} — verified numbers (as of {as_of})", title_style="bold")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_column("Formula", style="dim")
    table.add_column("✓", justify="center")
    for label, value, formula, mark in rows:
        table.add_row(label, value, formula, mark)
    console.print(table)


@app.command()
def metrics(
    ticker: str = typer.Argument(..., help="Ticker to compute SEC-XBRL verified numbers for."),
    as_of: str | None = typer.Option(
        None, "--as-of", help="Point-in-time date (YYYY-MM-DD); default today."
    ),
    backfill: int | None = typer.Option(
        None, "--backfill", help="Quarters of filing history to pull (default 8)."
    ),
) -> None:
    """Compute and print a company's verified numbers straight from SEC XBRL — deterministic,
    NO LLM key needed. Ingests the ticker if needed, runs the
    metrics engine, and prints the results. A fast way to see the trust layer work on real data."""
    from datetime import date

    from finwatch.digest.render import metric_view_rows
    from finwatch.metrics.service import MetricsService

    cfg = _config_or_exit()
    conn, service = build_service(cfg)
    quarters = backfill if backfill is not None else DEFAULT_BACKFILL_QUARTERS
    as_of_date = as_of or date.today().isoformat()
    try:
        ticker_u = ticker.strip().upper()
        company = service.repo.get_company_by_ticker(ticker_u)
        if company is None:
            try:
                company = service.add_holding(ticker_u)
            except TickerNotFoundError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(code=1) from exc
            console.print(
                f"[dim]{company.ticker} was not tracked — added to tracked tickers.[/]"
            )
        result = service.ingest_one(company.cik, backfill_quarters=quarters)
        if result.error:
            console.print(f"[yellow]![/] ingest note for {company.ticker}: {result.error}")
        ms = MetricsService(service.repo, lambda c: service.edgar.companyfacts(c))
        bundle, _ = ms.compute_and_store(company.cik, as_of=as_of_date)
        ticker_out = company.ticker
    finally:
        service.edgar.close()
        conn.close()

    rows = metric_view_rows(bundle)
    _print_metrics_table(ticker_out, as_of_date, rows)
    if not any(mark == "✓" for *_, mark in rows):
        console.print(
            f"[yellow]No verified financials for {ticker_out}.[/] The issuer may lack structured "
            f"XBRL (e.g. some foreign private issuers) or filed too little history to compute."
        )
    console.print(
        "[dim]Computed deterministically from SEC XBRL facts (never by an LLM). "
        "Run [bold]finwatch digest[/] to see them in report context.[/]"
    )


@app.command()
def digest(
    out: str | None = typer.Option(None, "--out", help="Also write the markdown to this path."),
) -> None:
    """Render the current verified markdown digest from the DB."""
    import json as _json
    from datetime import UTC, datetime

    from finwatch.db import Digest, Repo, init_db
    from finwatch.digest import render_digest

    cfg = _config_or_exit()
    conn = init_db(cfg.db_path)
    try:
        repo = Repo(conn)
        result = render_digest(repo)
        run_at = datetime.now(UTC).isoformat()
        if out:
            from pathlib import Path

            # UTF-8 explicitly: the digest always contains non-ASCII (→, ✓, ⚠) and the
            # platform default encoding (cp1252 on Windows, ASCII under LC_ALL=C) would crash.
            Path(out).write_text(result.markdown, encoding="utf-8")
            repo.insert_digest(
                Digest(
                    run_at=run_at,
                    since=None,
                    until=None,
                    markdown_path=out,
                    filings_json=_json.dumps(result.accessions),
                )
            )
            console.print(
                f"[green]✓[/] Digest written to {out} ({len(result.accessions)} filings)."
            )
        else:
            console.print(result.markdown)
    finally:
        conn.close()


@app.command()
def eval(
    models: str | None = typer.Option(
        None,
        "--models",
        help="Comma-separated litellm model strings to bake off (live). "
        "Omit to run the bundled recorded golden set (no API keys).",
    ),
) -> None:
    """Golden-set bake-off across candidate models (CLAUDE.md §16)."""
    from finwatch.evals.harness import bakeoff, render_report, run_live, run_recorded

    if not models:
        console.print(render_report(bakeoff([run_recorded()])))
        return

    cfg = _config_or_exit()
    from pathlib import Path

    from finwatch.ingest import EdgarClient

    cache_dir = Path(cfg.db_path).parent / "cache" if cfg.db_path != ":memory:" else None
    edgar = EdgarClient(cfg.sec_user_agent, cache_dir=cache_dir)
    try:
        reports = [run_live(m.strip(), edgar) for m in models.split(",") if m.strip()]
    finally:
        edgar.close()
    console.print(render_report(bakeoff(reports)))


@app.command()
def demo() -> None:
    """Run the full pipeline on bundled filings with ZERO API keys, then print a digest."""
    from finwatch.db import Repo
    from finwatch.demo import DEMO_SINCE, build_demo_db
    from finwatch.digest import render_digest

    conn = build_demo_db()
    try:
        result = render_digest(Repo(conn), since=DEMO_SINCE)
    finally:
        conn.close()
    console.print(result.markdown)


if __name__ == "__main__":  # pragma: no cover
    app()
