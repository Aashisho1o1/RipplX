"""CLI wiring for ticker-only add/ingest — fixture-backed service, no network."""
from __future__ import annotations

from datetime import date

import httpx
from typer.testing import CliRunner

from finwatch.cli import app
from finwatch.db import Repo, init_db
from finwatch.ingest import EdgarClient, IngestService

runner = CliRunner()
CIK = "0000320193"


def _install_fake_service(monkeypatch, tmp_path, mock_transport):
    """Patch cli.build_service to use a file-backed DB (persists across invocations)
    and a fixture-backed EDGAR client."""
    db_path = str(tmp_path / "finwatch.db")

    def fake_build_service(_cfg, conn=None):
        conn = init_db(db_path)
        edgar = EdgarClient(
            "UA test@example.com",
            client=httpx.Client(transport=mock_transport),
            sleep=lambda _s: None,
        )
        return conn, IngestService(Repo(conn), edgar, as_of=date(2024, 12, 1))

    monkeypatch.setenv("SEC_USER_AGENT", "UA test@example.com")
    monkeypatch.setattr("finwatch.cli.build_service", fake_build_service)
    return db_path


def test_add_then_ingest_via_cli(monkeypatch, tmp_path, mock_transport):
    db_path = _install_fake_service(monkeypatch, tmp_path, mock_transport)

    added = runner.invoke(app, ["add", "AAPL"])
    assert added.exit_code == 0, added.output
    assert "AAPL" in added.output

    ingested = runner.invoke(app, ["ingest"])
    assert ingested.exit_code == 0, ingested.output
    assert "3 filings" in ingested.output

    repo = Repo(init_db(db_path))
    assert repo.get_company(CIK).sic_code == "3571"
    assert repo.count_xbrl_facts(CIK) == 7


def test_ingest_with_no_holdings_prompts_to_add(monkeypatch, tmp_path, mock_transport):
    _install_fake_service(monkeypatch, tmp_path, mock_transport)
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0
    assert "No tracked companies" in result.output


def test_cli_ingest_renders_per_cik_errors(monkeypatch, tmp_path, mock_transport):
    # AAPL succeeds; MSFT has no fixtures -> its line renders the error branch.
    _install_fake_service(monkeypatch, tmp_path, mock_transport)
    runner.invoke(app, ["add", "AAPL"])
    runner.invoke(app, ["add", "MSFT"])
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0
    assert "AAPL" in result.output and "MSFT" in result.output
    assert "Ingest complete" in result.output
