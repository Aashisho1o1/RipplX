"""Non-secret persisted settings plus process-memory LLM credentials."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from finwatch.db.repositories import Repo

SETTING_USER_AGENT = "web.sec_user_agent"
SETTING_PERIOD = "web.period"
SETTING_MODEL_EXTRACT = "web.model_extract"
SETTING_MODEL_REASON = "web.model_reason"


@dataclass(frozen=True)
class ResolvedSettings:
    sec_user_agent: str | None
    period: str
    model_extract: str | None
    model_reason: str | None
    api_key_configured: bool
    api_key_source: str | None


class RuntimeSecrets:
    """Session credentials; values are intentionally never serializable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._api_key: str | None = None

    def set_api_key(self, value: str | None) -> None:
        with self._lock:
            self._api_key = value.strip() if value and value.strip() else None

    def api_key(self) -> str | None:
        with self._lock:
            return self._api_key


def _environment_key_configured() -> bool:
    names = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "AZURE_API_KEY",
    )
    return any(os.environ.get(name, "").strip() for name in names)


def resolve_settings(repo: Repo, secrets: RuntimeSecrets) -> ResolvedSettings:
    session_key = secrets.api_key()
    environment_key = _environment_key_configured()
    extract = repo.get_setting(SETTING_MODEL_EXTRACT) or os.environ.get("FINWATCH_MODEL_EXTRACT")
    reason = repo.get_setting(SETTING_MODEL_REASON) or os.environ.get("FINWATCH_MODEL_REASON")
    return ResolvedSettings(
        sec_user_agent=repo.get_setting(SETTING_USER_AGENT) or os.environ.get("SEC_USER_AGENT"),
        period=repo.get_setting(SETTING_PERIOD) or "90d",
        model_extract=extract or None,
        model_reason=reason or extract or None,
        api_key_configured=bool(session_key or environment_key),
        api_key_source="session" if session_key else "environment" if environment_key else None,
    )
