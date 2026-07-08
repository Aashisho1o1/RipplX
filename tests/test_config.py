"""Config loader tests: hard-fail without SEC_USER_AGENT; .env precedence."""
from __future__ import annotations

import pytest

from finwatch.config import Config, ConfigError, load_config


def test_load_config_hard_fails_without_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ConfigError):
        load_config(env_path=tmp_path / "missing.env")


def test_load_config_reads_user_agent_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    cfg = load_config(env_path=tmp_path / "missing.env")
    assert isinstance(cfg, Config)
    assert cfg.sec_user_agent == "Test User test@example.com"
    assert cfg.db_path == "./data/finwatch.db"


def test_real_env_wins_over_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('SEC_USER_AGENT="From File file@example.com"\n', encoding="utf-8")
    monkeypatch.setenv("SEC_USER_AGENT", "From Env env@example.com")
    cfg = load_config(env_path=env)
    assert cfg.sec_user_agent == "From Env env@example.com"


def test_dotenv_used_when_env_absent(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        '# comment line\nSEC_USER_AGENT="From File file@example.com"\n'
        "FINWATCH_PRICE_SOURCE=stooq\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.delenv("FINWATCH_PRICE_SOURCE", raising=False)
    cfg = load_config(env_path=env)
    assert cfg.sec_user_agent == "From File file@example.com"
