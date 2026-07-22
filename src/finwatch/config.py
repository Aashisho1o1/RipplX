"""Configuration loading for finwatch.

Reads a local ``.env`` (stdlib parser — no extra dependency) then the process
environment, and hard-fails without ``SEC_USER_AGENT`` because the SEC requires a
User-Agent header for all EDGAR access. Loading is lazy: commands that touch EDGAR
call :func:`load_config`; ``finwatch --help`` never does, so it works with no config.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, field_validator

DEFAULT_ENV_PATH = Path(".env")
# Launch accepts a small allowlist of litellm provider prefixes: OpenAI direct,
# OpenRouter (which proxies cheap models such as deepseek/gemini through one fixed,
# known endpoint), and z.ai (Zhipu GLM via its Anthropic-compatible coding endpoint,
# reached with ZAI_API_KEY). Each maps to exactly one known endpoint; other providers
# and arbitrary base-URL overrides stay out of the production path.
PRODUCTION_MODEL_PREFIXES = ("openai/", "openrouter/", "z-ai/")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Config(BaseModel):
    """Resolved runtime configuration."""

    sec_user_agent: str
    db_path: str = "./data/finwatch.db"
    model: str | None = None
    skeptic_model: str | None = None

    @field_validator("model", "skeptic_model")
    @classmethod
    def one_production_provider(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(PRODUCTION_MODEL_PREFIXES):
            raise ValueError(
                "configured model must use one of these production providers: "
                + ", ".join(PRODUCTION_MODEL_PREFIXES)
            )
        return value


def load_dotenv(path: Path = DEFAULT_ENV_PATH) -> None:
    """Minimal ``.env`` loader (stdlib only).

    Parses ``KEY=VALUE`` lines, strips surrounding quotes, ignores blanks and
    ``#`` comments, and uses ``setdefault`` so the real environment always wins.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> Config:
    """Load config from ``.env`` + environment; hard-fail if ``SEC_USER_AGENT`` is absent."""
    load_dotenv(env_path)
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        raise ConfigError(
            "SEC_USER_AGENT is required (SEC policy for EDGAR access). "
            "Set it in .env or the environment, e.g.\n"
            '  SEC_USER_AGENT="Your Name your-email@example.com"'
        )
    return Config(
        sec_user_agent=user_agent,
        db_path=os.environ.get("FINWATCH_DB", "./data/finwatch.db"),
        model=os.environ.get("FINWATCH_MODEL", "").strip() or None,
        skeptic_model=os.environ.get("FINWATCH_SKEPTIC_MODEL", "").strip() or None,
    )
