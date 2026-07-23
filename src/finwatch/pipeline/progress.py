"""Persisted, lightweight progress reporting for the per-filing pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from finwatch.db.repositories import Repo

PIPELINE_STAGES = ("download", "parse", "metrics", "extract", "verify")
STAGE_LABELS = {
    "download": "Downloaded",
    "parse": "Parsed",
    "extract": "Researched",
    "metrics": "Metrics computed",
    "verify": "Verified",
}

ProgressCallback = Callable[[str, str, str, dict], None]

# Closed vocabulary of stage-failure reasons that are safe to persist and project.
# These are finwatch's own typed terminal reasons (llm/harness.py) and compiler run
# codes — fixed identifiers that never carry provider text, filing content, credentials,
# or model-authored prose. Any other failure records no reason at all, so an unexpected
# exception can never leak its message into the stage ledger.
FAILURE_REASONS = frozenset({
    "provider_failed",
    "malformed_action_breakdown",
    "budget_exhausted",
    "compile_failed",
    "repair_compile_failed",
    "skeptic_blocked",
    "skeptic_incomplete",
    "form_scope",
    "critical_coverage",
})
FAILURE_REASON_LABELS = {
    "provider_failed": "The model provider could not be reached or rejected the request.",
    "malformed_action_breakdown": "The model did not return usable structured actions.",
    "budget_exhausted": "The bounded research budget ran out before a result was reached.",
    "compile_failed": "No candidate finding passed the deterministic compiler.",
    "repair_compile_failed": "The single allowed repair did not produce a compiling result.",
    "skeptic_blocked": "The finance reviewer's objections removed every candidate finding.",
    "skeptic_incomplete": "The finance review stage did not complete.",
    "form_scope": "The filing identity did not match the requested filing.",
    "critical_coverage": "A required critical finding was missing.",
}


def failure_reason(error: Exception | str) -> str | None:
    """Return a typed reason for ``error`` when it is in the closed vocabulary.

    Prefers an explicit ``reason`` attribute (HarnessError, StageError) over the
    exception message, so a wrapping layer that rephrases the message cannot silently
    lose the reason. Anything outside FAILURE_REASONS yields None.
    """
    if not isinstance(error, str):
        typed = getattr(error, "reason", None)
        if isinstance(typed, str) and typed.strip().lower() in FAILURE_REASONS:
            return typed.strip().lower()
    text = (error if isinstance(error, str) else str(error)).strip().lower()
    return text if text in FAILURE_REASONS else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StageReporter:
    def __init__(
        self,
        repo: Repo,
        accession_number: str,
        *,
        now_fn: Callable[[], str] = _now_iso,
        callback: ProgressCallback | None = None,
    ) -> None:
        self.repo = repo
        self.accession_number = accession_number
        self.now_fn = now_fn
        self.callback = callback

    def is_complete(self, stage: str) -> bool:
        row = self.repo.get_filing_stage(self.accession_number, stage)
        return bool(row and row.status in {"completed", "skipped"})

    def _set(
        self,
        stage: str,
        status: str,
        *,
        message: str,
        error: str | None = None,
        diagnostics: dict | None = None,
    ) -> None:
        details = diagnostics or {}
        self.repo.set_filing_stage(
            self.accession_number,
            stage,
            status,
            at=self.now_fn(),
            error=error,
            diagnostics=details,
        )
        if self.callback:
            self.callback(stage, status, message, details)

    def running(self, stage: str, diagnostics: dict | None = None) -> None:
        self._set(stage, "running", message=f"{STAGE_LABELS[stage]}…", diagnostics=diagnostics)

    def completed(
        self, stage: str, diagnostics: dict | None = None, *, message: str | None = None
    ) -> None:
        self._set(
            stage,
            "completed",
            message=message or STAGE_LABELS[stage],
            diagnostics=diagnostics,
        )

    def skipped(self, stage: str, reason: str) -> None:
        self._set(stage, "skipped", message=reason, diagnostics={"reason": reason})

    def failed(self, stage: str, error: Exception | str, diagnostics: dict | None = None) -> None:
        # Provider exceptions can include request headers or credentials, so the stage
        # ledger never stores raw exception text. It does store a typed reason when the
        # failure is one finwatch itself raised (FAILURE_REASONS): without that, every
        # failure is indistinguishable from every other and an operator cannot tell a
        # missing API key from a model that returned unusable JSON.
        detail = f"{STAGE_LABELS[stage]} could not be completed."
        details = dict(diagnostics or {})
        reason = failure_reason(error)
        if reason:
            details["reason"] = reason
        self._set(
            stage,
            "failed",
            message=detail,
            error=detail,
            diagnostics=details,
        )
