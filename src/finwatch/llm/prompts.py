"""Versioned prompt loader.

Prompts are DATA, not code (CLAUDE.md rule 7): stored verbatim under
``finwatch/prompts/``, loaded at runtime, and their ``prompt_version`` is recorded
with every analysis. The P1/P2/P3 stage prompts embed the shared foundation block
via a ``[FOUNDATION BLOCK]`` placeholder.
"""
from __future__ import annotations

import importlib.resources

PROMPT_SUITE_VERSION = "v1"
_FOUNDATION_PLACEHOLDER = "[FOUNDATION BLOCK]"

STAGE_P1 = "P1_extractor"
STAGE_P2 = "P2_impact"
STAGE_P3 = "P3_rationale"
_STAGE_VERSIONS = {STAGE_P1: "v2"}


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
    if _FOUNDATION_PLACEHOLDER in text:
        text = text.replace(_FOUNDATION_PLACEHOLDER, _read("foundation"))
        version = f"{version}+foundation.{PROMPT_SUITE_VERSION}"
    return text, version
