"""Non-secret persisted settings plus process-memory LLM credentials."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from finwatch.config import PRODUCTION_MODEL_PREFIXES
from finwatch.db.repositories import LOCAL_USER_ID, Repo

SETTING_USER_AGENT = "web.sec_user_agent"
LOCAL_SESSION_ID = "local"


@dataclass(frozen=True)
class ResolvedSettings:
    sec_user_agent: str | None
    period: str
    model: str | None
    api_key_configured: bool
    api_key_source: str | None


class RuntimeSecrets:
    """Provider credentials keyed by opaque browser session ID."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._api_keys: dict[str, tuple[str, int | None]] = {}

    def _prune_locked(self) -> None:
        now = self._clock()
        for session_id, (_key, expires_at) in list(self._api_keys.items()):
            if expires_at is not None and expires_at <= now:
                del self._api_keys[session_id]

    def set_api_key(
        self,
        session_id: str,
        value: str | None,
        *,
        expires_at: int | None = None,
    ) -> None:
        with self._lock:
            self._prune_locked()
            key = value.strip() if value and value.strip() else None
            if key:
                self._api_keys[session_id] = (key, expires_at)
            else:
                self._api_keys.pop(session_id, None)

    def api_key(self, session_id: str) -> str | None:
        with self._lock:
            self._prune_locked()
            stored = self._api_keys.get(session_id)
            return stored[0] if stored else None

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._api_keys.pop(session_id, None)


def _environment_key_for(model: str | None) -> bool:
    # The credential must match the configured model's provider: openai/* reads
    # OPENAI_API_KEY, openrouter/* reads OPENROUTER_API_KEY, z-ai/* reads ZAI_API_KEY. A
    # key for the OTHER provider does NOT enable analysis (litellm would route by the
    # model prefix and never see it), so it must not report the model as ready.
    if model and model.startswith("openai/"):
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if model and model.startswith("openrouter/"):
        return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    if model and model.startswith("z-ai/"):
        return bool(os.environ.get("ZAI_API_KEY", "").strip())
    return False


def production_model() -> str | None:
    model = os.environ.get("FINWATCH_MODEL", "").strip()
    if model and not model.startswith(PRODUCTION_MODEL_PREFIXES):
        raise RuntimeError(
            "FINWATCH_MODEL must use one of these production providers: "
            + ", ".join(PRODUCTION_MODEL_PREFIXES)
        )
    return model or None


def provider_for_model(model: str | None) -> str | None:
    if model and model.startswith("openai/"):
        return "OpenAI"
    if model and model.startswith("openrouter/"):
        return "OpenRouter"
    if model and model.startswith("z-ai/"):
        return "z.ai"
    return None


def resolve_settings(
    repo: Repo,
    secrets: RuntimeSecrets,
    *,
    user_id: str = LOCAL_USER_ID,
    session_id: str = LOCAL_SESSION_ID,
    remote: bool = False,
) -> ResolvedSettings:
    model = production_model()
    session_key = secrets.api_key(session_id)
    # Hosted participants must bring their own key. Environment credentials remain
    # available only to the local browser and CLI operator.
    environment_key = not remote and _environment_key_for(model)
    return ResolvedSettings(
        sec_user_agent=(
            os.environ.get("SEC_USER_AGENT")
            if remote
            else repo.get_setting(SETTING_USER_AGENT) or os.environ.get("SEC_USER_AGENT")
        ),
        period=repo.get_user_period(user_id) or "90d",
        model=model,
        api_key_configured=bool(session_key or environment_key),
        api_key_source="session" if session_key else "environment" if environment_key else None,
    )
