"""finwatch command-line interface (Typer).

Phase 0 provides the full command skeleton mirroring CLAUDE.md §5. Each command is
stubbed and marks the phase that will implement it. ``finwatch init`` already wires
the config hard-fail so the SEC_USER_AGENT requirement is live and testable.
"""
from __future__ import annotations

import typer
from rich.console import Console

from finwatch import __version__
from finwatch.config import ConfigError, load_config

console = Console()

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
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]✓[/] SEC_USER_AGENT configured. Database: {cfg.db_path}")
    _stub("Phase 1", "db + schema creation")


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
    _stub("Phase 1")


@app.command()
def watch(
    ticker: str = typer.Argument(..., help="Ticker to track without ownership."),
) -> None:
    """Track a company without ownership (company-level read, no signal)."""
    _stub("Phase 1")


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Ticker to analyze ad-hoc."),
) -> None:
    """Ad-hoc analysis with watch semantics (not persisted)."""
    _stub("Phase 5")


@app.command()
def ingest(
    backfill: int | None = typer.Option(
        None, "--backfill", help="Quarters of history to backfill."),
) -> None:
    """Pull filings + companyfacts (and EOD prices) for tracked CIKs."""
    _stub("Phase 1")


@app.command()
def digest(
    since: str | None = typer.Option(
        None, "--since", help="Only include filings since this date (YYYY-MM-DD)."),
    signals: bool = typer.Option(
        False, "--signals", help="Also render the (unvalidated) shadow-signal block."),
) -> None:
    """Render the markdown digest (``--signals`` gated, OFF by default)."""
    _stub("Phase 7")


@app.command()
def eval(
    models: str = typer.Option(
        ..., "--models", help="Comma-separated litellm model strings to bake off."),
) -> None:
    """Golden-set bake-off across candidate models."""
    _stub("Phase 5")


@app.command()
def verify(
    accession: str = typer.Argument(..., help="Accession number to re-verify."),
) -> None:
    """Re-run the deterministic verifier on a stored analysis."""
    _stub("Phase 4")


@app.command()
def demo() -> None:
    """Run on bundled cached filings with zero API keys."""
    _stub("Phase 7")


@shadow_app.command("report")
def shadow_report() -> None:
    """Show the shadow-signal track record."""
    _stub("Phase 6")


if __name__ == "__main__":  # pragma: no cover
    app()
