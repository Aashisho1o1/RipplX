"""Versioned prompt loader.

Prompts are DATA, not code (CLAUDE.md rule 7): stored verbatim under
``finwatch/prompts/``, loaded at runtime, and their ``prompt_version`` is recorded
with every analysis. The P1/P2/P3 stage prompts embed the shared foundation block
via a ``[FOUNDATION BLOCK]`` placeholder.
"""
from __future__ import annotations

import importlib.resources

PROMPT_SUITE_VERSION = "v2"
_FOUNDATION_PLACEHOLDER = "[FOUNDATION BLOCK]"

STAGE_P1 = "P1_extractor"
_STAGE_VERSIONS = {STAGE_P1: "v4"}
# Stage prompts MUST embed the shared foundation block (untrusted-input / prompt-injection
# defense). ``foundation`` itself is not a stage and carries no placeholder.
_STAGE_PROMPTS = frozenset({STAGE_P1})


def _read(name: str) -> str:
    return (
        importlib.resources.files("finwatch.prompts")
        .joinpath(f"{name}.md")
        .read_text(encoding="utf-8")
    )


def load_prompt(stage: str) -> tuple[str, str]:
    """Return ``(system_prompt, prompt_version)`` for a stage, with the foundation
    block spliced in where the placeholder appears."""
    text = _read(stage)
    version = f"{stage}.{_STAGE_VERSIONS.get(stage, PROMPT_SUITE_VERSION)}"
    # Fail closed if a stage prompt has lost its foundation placeholder: silently
    # shipping a stage without the injection-defense block (the only visible signal
    # being a dropped ``+foundation`` version suffix) is exactly the latent footgun
    # this guards against. Require exactly one placeholder; ``foundation`` is exempt.
    placeholder_count = text.count(_FOUNDATION_PLACEHOLDER)
    if stage in _STAGE_PROMPTS and placeholder_count != 1:
        raise ValueError(
            f"stage prompt {stage!r} must contain exactly one {_FOUNDATION_PLACEHOLDER} "
            f"placeholder (found {placeholder_count}); the foundation block must never "
            f"be silently omitted"
        )
    if _FOUNDATION_PLACEHOLDER in text:
        text = text.replace(_FOUNDATION_PLACEHOLDER, _read("foundation"))
        version = f"{version}+foundation.{PROMPT_SUITE_VERSION}"
    return text, version
