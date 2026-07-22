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


def test_load_config_accepts_allowlisted_production_providers(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    for model in (
        "openai/evaluated-model",
        "openrouter/deepseek/deepseek-v4-flash",
        "z-ai/glm-5.2",
    ):
        monkeypatch.setenv("FINWATCH_MODEL", model)
        assert load_config(env_path=tmp_path / "missing.env").model == model

    # A raw anthropic/ model is still rejected — only the z-ai/ prefix (which maps to the
    # z.ai Anthropic-compatible endpoint) is an allowlisted production provider.
    monkeypatch.setenv("FINWATCH_MODEL", "anthropic/other-model")
    with pytest.raises(ValueError, match="production providers"):
        load_config(env_path=tmp_path / "missing.env")


def test_optional_skeptic_model_uses_the_same_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("FINWATCH_SKEPTIC_MODEL", "openrouter/deepseek/deepseek-chat")
    assert load_config(
        env_path=tmp_path / "missing.env"
    ).skeptic_model == "openrouter/deepseek/deepseek-chat"

    monkeypatch.setenv("FINWATCH_SKEPTIC_MODEL", "anthropic/other-model")
    with pytest.raises(ValueError, match="production providers"):
        load_config(env_path=tmp_path / "missing.env")


def test_real_env_wins_over_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('SEC_USER_AGENT="From File file@example.com"\n', encoding="utf-8")
    monkeypatch.setenv("SEC_USER_AGENT", "From Env env@example.com")
    cfg = load_config(env_path=env)
    assert cfg.sec_user_agent == "From Env env@example.com"


def test_dotenv_used_when_env_absent(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        '# comment line\nSEC_USER_AGENT="From File file@example.com"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    cfg = load_config(env_path=env)
    assert cfg.sec_user_agent == "From File file@example.com"
