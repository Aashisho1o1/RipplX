"""finwatch command-line interface (Typer).

Phase 0 provides the full command skeleton mirroring CLAUDE.md §5. Each command is
stubbed and marks the phase that will implement it. ``finwatch init`` already wires
the config hard-fail so the SEC_USER_AGENT requirement is live and testable.
"""
from __future__ import annotations

import typer
from rich.console import Console

from finwatch import __version__
from finwatch.config import Config, ConfigError, load_config
from finwatch.ingest import (
    DEFAULT_BACKFILL_QUARTERS,
    TickerNotFoundError,
    build_service,
)

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
shadow_app = typer.Typer(help="Shadow-signal track record.", no_args_is_help=True)
app.add_typer(shadow_app, name="shadow")


def _stub(phase: str, what: str = "") -> None:
    tail = f" ({what})" if what else ""
    console.print(f"[yellow]not yet implemented[/] — arrives in {phase}{tail}.")
    raise typer.Exit(code=0)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"finwatch {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
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
def add(
    ticker: str = typer.Argument(..., help="Ticker to add as an owned holding."),
    shares: float = typer.Option(..., "--shares", help="Number of shares held."),
    cost: float = typer.Option(..., "--cost", help="Cost basis per share."),
    target_weight: float | None = typer.Option(
        None, "--target-weight", help="Target portfolio weight (%)."),
    horizon: str | None = typer.Option(
        None, "--horizon", help="Holding horizon: trading|1-3y|5y+|indefinite."),
    thesis: str | None = typer.Option(
        None, "--thesis", help="Investment thesis (OPTIONAL by design)."),
) -> None:
    """Add an owned holding (thesis optional by design)."""
    cfg = _config_or_exit()
    conn, service = build_service(cfg)
    try:
        company = service.add_holding(
            ticker, owned=True, shares=shares, cost_basis=cost,
            target_weight_pct=target_weight, horizon=horizon, thesis=thesis,
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
    """Track a company without ownership (company-level read, no signal)."""
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


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Ticker to analyze ad-hoc."),
) -> None:
    """Ad-hoc analysis with watch semantics (not persisted)."""
    _stub("Phase 5")


@app.command()
def ingest(
    backfill: int | None = typer.Option(
        None, "--backfill", help="Quarters of filing history to index (default 8)."),
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
def digest(
    since: str | None = typer.Option(
        None, "--since", help="Only include filings since this date (YYYY-MM-DD)."),
    until: str | None = typer.Option(
        None, "--until", help="Only include filings up to this date (YYYY-MM-DD)."),
    signals: bool = typer.Option(
        False, "--signals", help="Also render the (unvalidated) shadow-signal block."),
    out: str | None = typer.Option(
        None, "--out", help="Also write the markdown to this path."),
) -> None:
    """Render the markdown digest from the DB (``--signals`` gated, OFF by default)."""
    import json as _json
    from datetime import UTC, datetime

    from finwatch.db import Digest, Repo, init_db
    from finwatch.digest import render_digest

    cfg = _config_or_exit()
    conn = init_db(cfg.db_path)
    try:
        repo = Repo(conn)
        result = render_digest(repo, since=since, until=until, include_signals=signals)
        run_at = datetime.now(UTC).isoformat()
        if out:
            from pathlib import Path
            # UTF-8 explicitly: the digest always contains non-ASCII (→, ✓, ⚠) and the
            # platform default encoding (cp1252 on Windows, ASCII under LC_ALL=C) would crash.
            Path(out).write_text(result.markdown, encoding="utf-8")
            repo.insert_digest(Digest(run_at=run_at, since=since, until=until,
                                      markdown_path=out,
                                      filings_json=_json.dumps(result.accessions)))
            console.print(f"[green]✓[/] Digest written to {out} "
                          f"({len(result.accessions)} filings).")
        else:
            console.print(result.markdown)
    finally:
        conn.close()


@app.command()
def eval(
    models: str | None = typer.Option(
        None, "--models",
        help="Comma-separated litellm model strings to bake off (live). "
             "Omit to run the bundled recorded golden set (no API keys)."),
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
    """Re-run the deterministic verifier on a stored analysis."""
    _stub("Phase 4")


@app.command()
def demo(
    signals: bool = typer.Option(
        False, "--signals", help="Also render the (unvalidated) shadow-signal block."),
) -> None:
    """Run the full pipeline on bundled filings with ZERO API keys, then print a digest."""
    from finwatch.db import Repo
    from finwatch.demo import DEMO_SINCE, build_demo_db
    from finwatch.digest import render_digest

    conn = build_demo_db()
    try:
        result = render_digest(Repo(conn), since=DEMO_SINCE, include_signals=signals)
    finally:
        conn.close()
    console.print(result.markdown)
    if not signals:
        console.print("\n[dim]Re-run with [bold]finwatch demo --signals[/] to see the "
                      "unvalidated shadow-signal block.[/]")


@shadow_app.command("report")
def shadow_report() -> None:
    """Show the shadow-signal track record."""
    from finwatch.db import Repo, init_db
    from finwatch.signals.engine import render_shadow_report

    cfg = _config_or_exit()
    conn = init_db(cfg.db_path)
    try:
        console.print(render_shadow_report(Repo(conn).list_shadow_log()))
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    app()
