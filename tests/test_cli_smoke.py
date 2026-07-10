"""CLI smoke tests: help, version, and the init config hard-fail."""
from __future__ import annotations

from typer.testing import CliRunner

from finwatch.cli import app

runner = CliRunner()


def test_help_works():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "finwatch" in result.output.lower()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "finwatch" in result.output.lower()


def test_every_command_exposes_help():
    for cmd in ("init", "add", "watch", "analyze", "ingest", "digest",
                "eval", "verify", "demo"):
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"
    assert runner.invoke(app, ["shadow", "report", "--help"]).exit_code != 0


def test_init_hard_fails_without_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env present here
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1


def test_init_succeeds_with_user_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def test_init_creates_database_file(monkeypatch, tmp_path):
    db = tmp_path / "data" / "finwatch.db"
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("FINWATCH_DB", str(db))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert db.exists()
