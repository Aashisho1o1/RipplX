"""Non-secret persisted settings plus process-memory LLM credentials."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from finwatch.config import PRODUCTION_MODEL_PREFIXES
from finwatch.db.repositories import Repo

SETTING_USER_AGENT = "web.sec_user_agent"
SETTING_PERIOD = "web.period"


@dataclass(frozen=True)
class ResolvedSettings:
    sec_user_agent: str | None
    period: str
    model: str | None
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
    # openai/* reads OPENAI_API_KEY; openrouter/* reads OPENROUTER_API_KEY. Either
    # environment credential counts as configured for the matching model prefix.
    return bool(
        os.environ.get("OPENAI_API_KEY", "").strip()
        or os.environ.get("OPENROUTER_API_KEY", "").strip()
    )


def production_model() -> str | None:
    model = os.environ.get("FINWATCH_MODEL", "").strip()
    if model and not model.startswith(PRODUCTION_MODEL_PREFIXES):
        raise RuntimeError(
            "FINWATCH_MODEL must use one of these production providers: "
            + ", ".join(PRODUCTION_MODEL_PREFIXES)
        )
    return model or None


def resolve_settings(repo: Repo, secrets: RuntimeSecrets) -> ResolvedSettings:
    session_key = secrets.api_key()
    environment_key = _environment_key_configured()
    return ResolvedSettings(
        sec_user_agent=repo.get_setting(SETTING_USER_AGENT) or os.environ.get("SEC_USER_AGENT"),
        period=repo.get_setting(SETTING_PERIOD) or "90d",
        model=production_model(),
        api_key_configured=bool(session_key or environment_key),
        api_key_source="session" if session_key else "environment" if environment_key else None,
    )
