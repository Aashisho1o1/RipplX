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
from finwatch.config import Config, ConfigError, load_config
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
    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
        console.print(
            "[yellow]Refusing a non-loopback bind.[/] Pass [bold]--allow-remote[/] "
            "only if you understand that this prototype has no authentication."
        )
        raise typer.Exit(code=1)
    try:
        import uvicorn

        from finwatch.web import create_app
    except (ImportError, RuntimeError) as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(code=1) from exc
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@app.command()
def add(
    ticker: str = typer.Argument(..., help="Ticker to add as an owned holding."),
    shares: float = typer.Option(..., "--shares", help="Number of shares held."),
    cost: float = typer.Option(..., "--cost", help="Cost basis per share."),
    target_weight: float | None = typer.Option(
        None, "--target-weight", help="Target portfolio weight (%)."
    ),
    horizon: str | None = typer.Option(
        None, "--horizon", help="Holding horizon: trading|1-3y|5y+|indefinite."
    ),
    thesis: str | None = typer.Option(
        None, "--thesis", help="Investment thesis (OPTIONAL by design)."
    ),
) -> None:
    """Add an owned holding (thesis optional by design)."""
    cfg = _config_or_exit()
    conn, service = build_service(cfg)
    try:
        company = service.add_holding(
            ticker,
            owned=True,
            shares=shares,
            cost_basis=cost,
            target_weight_pct=target_weight,
            horizon=horizon,
            thesis=thesis,
        )
    except TickerNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    finally:
        service.edgar.close()
        service.stooq.close()
        conn.close()
    console.print(
        f"[green]✓[/] Added [bold]{company.ticker}[/] (CIK {company.cik}) as an owned "
        f"holding. Run [bold]finwatch ingest[/] to pull filings, XBRL facts, and prices."
    )


@app.command()
def watch(
    ticker: str = typer.Argument(..., help="Ticker to track without ownership."),
) -> None:
    """Track a company without ownership context."""
    cfg = _config_or_exit()
    conn, service = build_service(cfg)
    try:
        company = service.add_holding(ticker, owned=False)
    except TickerNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    finally:
        service.edgar.close()
        service.stooq.close()
        conn.close()
    console.print(
        f"[green]✓[/] Watching [bold]{company.ticker}[/] (CIK {company.cik}). "
        f"Run [bold]finwatch ingest[/] to pull filings and financials."
    )


def _require_models(cfg: Config) -> None:
    if not cfg.model_extract or not cfg.model_reason:
        console.print(
            "[red]LLM models not configured.[/] Set [bold]FINWATCH_MODEL_EXTRACT[/] and "
            "[bold]FINWATCH_MODEL_REASON[/] (litellm model strings) in your .env to run the "
            "analysis pipeline. No key is needed for [bold]finwatch demo[/]."
        )
        raise typer.Exit(code=1)


def _run_pipeline(cfg: Config, *, cik: str | None, limit: int | None):
    """Build the production Orchestrator (real EdgarClient + LiteLLM) and process every
    not-yet-analyzed filing (optionally one CIK / capped). Returns the result list."""
    from pathlib import Path

    from finwatch.db import Repo, init_db
    from finwatch.ingest import EdgarClient
    from finwatch.llm.router import LiteLLMClient
    from finwatch.pipeline.run import build_orchestrator, process_tracked

    conn = init_db(cfg.db_path)
    repo = Repo(conn)
    cache_dir = Path(cfg.db_path).parent / "cache" if cfg.db_path != ":memory:" else None
    edgar = EdgarClient(cfg.sec_user_agent, cache_dir=cache_dir)
    orch = build_orchestrator(
        repo,
        llm_extract=LiteLLMClient(cfg.model_extract),
        llm_reason=LiteLLMClient(cfg.model_reason),
        companyfacts_provider=lambda c: edgar.companyfacts(c),
        price_provider=repo,
        model_extract=cfg.model_extract,
        model_reason=cfg.model_reason,
    )

    def fetch_html(url: str) -> str:
        return edgar.fetch_primary_doc(url).decode("utf-8", "replace")

    try:
        return process_tracked(repo, orch, fetch_html, cik=cik, limit=limit)
    finally:
        edgar.close()
        conn.close()


def _print_pipeline_results(results) -> None:
    for r in results:
        if r.ok:
            mark = "[yellow]⚠ manual review[/]" if r.manual_review else f"[green]{r.verdict}[/]"
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
    limit: int | None = typer.Option(
        None, "--limit", help="Max filings to analyze (default: all not-yet-analyzed)."
    ),
) -> None:
    """Run the analysis pipeline over a tracked ticker's ingested filings (watch semantics
    if it is not an owned holding); does not add a holding."""
    from finwatch.db import Repo, init_db

    cfg = _config_or_exit()
    _require_models(cfg)
    conn = init_db(cfg.db_path)
    company = Repo(conn).get_company_by_ticker(ticker)
    conn.close()
    if company is None:
        console.print(
            f"[red]{ticker} is not tracked.[/] Run [bold]finwatch watch {ticker}[/] "
            f"(or [bold]finwatch add {ticker} ...[/]) then [bold]finwatch ingest[/] first."
        )
        raise typer.Exit(code=1)
    results = _run_pipeline(cfg, cik=company.cik, limit=limit)
    if not results:
        console.print(
            f"No un-analyzed filings for {ticker}. Run [bold]finwatch ingest[/] "
            f"to pull new filings."
        )
        return
    _print_pipeline_results(results)


@app.command()
def ingest(
    backfill: int | None = typer.Option(
        None, "--backfill", help="Quarters of filing history to index (default 8)."
    ),
) -> None:
    """Pull filings + companyfacts (and EOD prices) for tracked CIKs."""
    cfg = _config_or_exit()
    conn, service = build_service(cfg)
    quarters = backfill if backfill is not None else DEFAULT_BACKFILL_QUARTERS
    try:
        if not service.repo.list_tracked_ciks():
            console.print(
                "No tracked companies yet. Add one with "
                "[bold]finwatch add TICKER --shares N --cost X[/] or "
                "[bold]finwatch watch TICKER[/]."
            )
            return
        summary = service.ingest_all(backfill_quarters=quarters)
    finally:
        service.edgar.close()
        service.stooq.close()
        conn.close()

    for r in summary.results:
        if r.error:
            console.print(
                f"[yellow]![/] {r.ticker}: {r.filings_indexed} filings, "
                f"{r.xbrl_facts} facts, {r.prices} prices — [red]{r.error}[/]"
            )
        else:
            console.print(
                f"[green]✓[/] {r.ticker}: {r.filings_indexed} filings "
                f"({r.filings_new} new), {r.xbrl_facts} XBRL facts, {r.prices} prices"
            )
    console.print(
        f"[bold]Ingest complete:[/] {summary.companies} companies, "
        f"{summary.filings} filings, {summary.xbrl_facts} XBRL facts, {summary.prices} prices."
    )


@app.command()
def process(
    ticker: str | None = typer.Option(
        None, "--ticker", help="Only process this tracked ticker's filings."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Max filings to process this run."),
) -> None:
    """Run the analysis pipeline (P0→P1→metrics→P2→verify) over ingested-but-not-yet-
    analyzed filings, persisting the analyses the digest renders from."""
    from finwatch.db import Repo, init_db

    cfg = _config_or_exit()
    _require_models(cfg)
    cik = None
    if ticker:
        conn = init_db(cfg.db_path)
        company = Repo(conn).get_company_by_ticker(ticker)
        conn.close()
        if company is None:
            console.print(
                f"[red]{ticker} is not tracked.[/] "
                f"[bold]finwatch watch {ticker} && finwatch ingest[/] first."
            )
            raise typer.Exit(code=1)
        cik = company.cik
    results = _run_pipeline(cfg, cik=cik, limit=limit)
    if not results:
        console.print("No un-analyzed filings. Run [bold]finwatch ingest[/] to pull filings.")
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
    show_all: bool = typer.Option(
        False, "--all", help="Show every computed metric, not just the digest starter set."
    ),
) -> None:
    """Compute and print a company's verified numbers straight from SEC XBRL — deterministic,
    NO LLM key needed. Ingests the ticker if needed (adding it as a watch entry), runs the
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
                company = service.add_holding(ticker_u, owned=False)
            except TickerNotFoundError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(code=1) from exc
            console.print(
                f"[dim]{company.ticker} was not tracked — added as a watch entry (non-owned) "
                f"so its metrics can be computed.[/]"
            )
        result = service.ingest_one(company.cik, backfill_quarters=quarters)
        if result.error:
            console.print(f"[yellow]![/] ingest note for {company.ticker}: {result.error}")
        ms = MetricsService(service.repo, service.repo, lambda c: service.edgar.companyfacts(c))
        bundle, _ = ms.compute_and_store(company.cik, as_of=as_of_date)
        ticker_out = company.ticker
    finally:
        service.edgar.close()
        service.stooq.close()
        conn.close()

    rows = metric_view_rows(bundle, show_all=show_all)
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
    since: str | None = typer.Option(
        None, "--since", help="Only include filings since this date (YYYY-MM-DD)."
    ),
    until: str | None = typer.Option(
        None, "--until", help="Only include filings up to this date (YYYY-MM-DD)."
    ),
    out: str | None = typer.Option(None, "--out", help="Also write the markdown to this path."),
) -> None:
    """Render the verified markdown digest from the DB."""
    import json as _json
    from datetime import UTC, datetime

    from finwatch.db import Digest, Repo, init_db
    from finwatch.digest import render_digest

    cfg = _config_or_exit()
    conn = init_db(cfg.db_path)
    try:
        repo = Repo(conn)
        result = render_digest(repo, since=since, until=until)
        run_at = datetime.now(UTC).isoformat()
        if out:
            from pathlib import Path

            # UTF-8 explicitly: the digest always contains non-ASCII (→, ✓, ⚠) and the
            # platform default encoding (cp1252 on Windows, ASCII under LC_ALL=C) would crash.
            Path(out).write_text(result.markdown, encoding="utf-8")
            repo.insert_digest(
                Digest(
                    run_at=run_at,
                    since=since,
                    until=until,
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
def verify(
    accession: str = typer.Argument(..., help="Accession number to re-verify."),
) -> None:
    """Re-run the deterministic verifier (V1/V4/V5) on a stored analysis — offline, from
    the DB, no LLM or network."""
    from finwatch.db import Repo, init_db
    from finwatch.pipeline.run import reverify

    cfg = _config_or_exit()
    conn = init_db(cfg.db_path)
    try:
        report = reverify(Repo(conn), accession)
    finally:
        conn.close()
    if report is None:
        console.print(
            f"[red]No stored analysis for {accession}.[/] Run [bold]finwatch process[/] first."
        )
        raise typer.Exit(code=1)
    colour = {"PASS": "green", "PASS_WITH_WARNINGS": "yellow", "FAIL": "red"}[report.verdict]
    console.print(f"[{colour}]{report.verdict}[/] — {accession}")
    for c in report.results:
        console.print(f"  {c.check_id}: {c.verdict}" + (f" — {c.detail}" if c.detail else ""))


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
