"""Configuration loading for finwatch.

Reads a local ``.env`` (stdlib parser — no extra dependency) then the process
environment, and hard-fails without ``SEC_USER_AGENT`` because the SEC requires a
User-Agent header for all EDGAR access. Loading is lazy: commands that touch EDGAR
call :func:`load_config`; ``finwatch --help`` never does, so it works with no config.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

DEFAULT_ENV_PATH = Path(".env")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Config(BaseModel):
    """Resolved runtime configuration."""

    sec_user_agent: str
    db_path: str = "./data/finwatch.db"
    model_extract: str | None = None
    model_reason: str | None = None


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
        model_extract=os.environ.get("FINWATCH_MODEL_EXTRACT") or None,
        model_reason=os.environ.get("FINWATCH_MODEL_REASON") or None,
    )
