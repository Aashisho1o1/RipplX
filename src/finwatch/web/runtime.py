"""Non-secret persisted settings plus operator-managed LLM credentials."""

from __future__ import annotations

import os
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


def environment_api_key(model: str | None) -> str | None:
    """Return the operator's provider key for ``model``, or None.

    The credential must match the configured model's provider: openai/* reads
    OPENAI_API_KEY, openrouter/* reads OPENROUTER_API_KEY, z-ai/* reads ZAI_API_KEY. A
    key for the OTHER provider does NOT enable analysis (litellm would route by the
    model prefix and never see it), so a mismatched key must never report the model as
    ready — that mismatch is silent at request time and surfaces only as a provider
    failure deep inside the run.
    """
    if model and model.startswith("openai/"):
        return os.environ.get("OPENAI_API_KEY", "").strip() or None
    if model and model.startswith("openrouter/"):
        return os.environ.get("OPENROUTER_API_KEY", "").strip() or None
    if model and model.startswith("z-ai/"):
        return os.environ.get("ZAI_API_KEY", "").strip() or None
    return None


def _environment_key_for(model: str | None) -> bool:
    return environment_api_key(model) is not None


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
    *,
    user_id: str = LOCAL_USER_ID,
    remote: bool = False,
) -> ResolvedSettings:
    model = production_model()
    environment_key = _environment_key_for(model)
    return ResolvedSettings(
        sec_user_agent=(
            os.environ.get("SEC_USER_AGENT")
            if remote
            else repo.get_setting(SETTING_USER_AGENT) or os.environ.get("SEC_USER_AGENT")
        ),
        period=repo.get_user_period(user_id) or "90d",
        model=model,
        api_key_configured=environment_key,
    )
