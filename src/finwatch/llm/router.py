"""Provider-agnostic LLM router.

The model is config, not architecture (CLAUDE.md §16): the golden-set bake-off picks
the cheapest model that clears the thresholds, and it arrives as a litellm model
string from the environment. ``litellm`` is imported lazily inside ``LiteLLMClient``
so tests and non-LLM code paths never pay its (heavy) import cost — tests drive a
``FakeLLMClient`` with recorded responses (no network, per the build rules).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# z.ai (Zhipu) serves GLM through an OpenAI-compatible endpoint, reached via litellm's
# ``openai`` provider with this fixed base URL and a ``ZAI_API_KEY``. This is the coding
# endpoint a GLM Coding Plan key is authorized for, and it supports ``response_format``
# json_object, so JSON mode is enabled and the provider enforces valid JSON. The earlier
# Anthropic-compatible endpoint (/api/anthropic) had no json_object and a translation
# bridge that dropped content and repeated tool calls on long agentic loops. The ``z-ai/``
# prefix keeps the finwatch-facing model string in the same one-token provider form as
# openai/openrouter.
ZAI_PREFIX = "z-ai/"
_ZAI_API_BASE = "https://api.z.ai/api/coding/paas/v4/"


def resolve_model(model: str) -> tuple[str, str | None, bool]:
    """Map a finwatch model string to (litellm_model, api_base, json_object_supported)."""
    if model.startswith(ZAI_PREFIX):
        return "openai/" + model[len(ZAI_PREFIX):], _ZAI_API_BASE, True
    return model, None, True

LAUNCH_MAX_OUTPUT_TOKENS = 2_000


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


class LLMClient(Protocol):
    def complete(
        self, *, system: str, user: str, temperature: float = 0.0,
        json_mode: bool = True, max_tokens: int = LAUNCH_MAX_OUTPUT_TOKENS,
    ) -> LLMResponse: ...


class LiteLLMClient:
    """Real client. Any litellm-supported provider via its model string."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 120.0,
        num_retries: int = 2,
    ) -> None:
        if not model:
            raise ValueError("LiteLLMClient requires a model string")
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self.num_retries = num_retries

    def complete(
        self, *, system: str, user: str, temperature: float = 0.0,
        json_mode: bool = True, max_tokens: int = LAUNCH_MAX_OUTPUT_TOKENS,
    ) -> LLMResponse:
        import litellm  # lazy: heavy import, only when a real call is made

        litellm_model, api_base, json_supported = resolve_model(self.model)
        kwargs: dict = {
            "model": litellm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
            "num_retries": self.num_retries,
        }
        if api_base:
            kwargs["api_base"] = api_base
        if json_mode and json_supported:
            kwargs["response_format"] = {"type": "json_object"}
        # GLM's thinking mode roughly quadruples malformed strict-JSON actions in this
        # harness (measured 4/8 vs 1/8 malformed on the same filing) and adds no quality
        # the tool loop and compiler don't already provide, so it is disabled for z.ai.
        if self.model.startswith(ZAI_PREFIX):
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        # z.ai keys live in ZAI_API_KEY; litellm's openai provider would otherwise look
        # for OPENAI_API_KEY, so pass it explicitly when none was supplied.
        api_key = self._api_key
        if api_key is None and self.model.startswith(ZAI_PREFIX):
            api_key = os.environ.get("ZAI_API_KEY", "").strip() or None
        if api_key:
            kwargs["api_key"] = api_key
        resp = litellm.completion(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        cost: float | None
        try:
            cost = litellm.completion_cost(completion_response=resp)
        except Exception:  # noqa: BLE001 — cost is best-effort, never fatal
            cost = None
        return LLMResponse(
            text=text,
            model=self.model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            cost_usd=cost,
        )


@dataclass
class FakeLLMClient:
    """Deterministic client for tests. Supply a ``responder(system, user) -> text`` to
    route by content, or ``responses`` consumed in order. Records every call."""

    responder: Callable[[str, str], str] | None = None
    responses: list[str] | None = None
    model: str = "fake/model"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(
        self, *, system: str, user: str, temperature: float = 0.0,
        json_mode: bool = True, max_tokens: int = LAUNCH_MAX_OUTPUT_TOKENS,
    ) -> LLMResponse:
        self.calls.append((system, user))
        if self.responder is not None:
            text = self.responder(system, user)
        elif self.responses:
            text = self.responses.pop(0)
        else:
            raise RuntimeError("FakeLLMClient: configure responder= or responses=")
        return LLMResponse(
            text=text,
            model=self.model,
            tokens_in=max(1, len(user) // 4),
            tokens_out=max(1, len(text) // 4),
        )


def extract_json(text: str) -> dict:
    """Parse a JSON object from an LLM response, tolerating ```json fences / prose."""
    s = text.strip()
    if s.startswith("```"):
        inner = s[3:]
        if inner[:4].lower() == "json":
            inner = inner[4:]
        end = inner.rfind("```")
        s = (inner[:end] if end != -1 else inner).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j > i:
            return json.loads(s[i : j + 1])
        raise
