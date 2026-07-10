"""Persisted, lightweight progress reporting for the per-filing pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from finwatch.db.repositories import Repo

PIPELINE_STAGES = ("download", "parse", "extract", "metrics", "impact", "verify")
STAGE_LABELS = {
    "download": "Downloaded",
    "parse": "Parsed",
    "extract": "Extracted",
    "metrics": "Metrics computed",
    "impact": "Impact assessed",
    "verify": "Verified",
}

ProgressCallback = Callable[[str, str, str, dict], None]


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
        detail = str(error)
        self._set(
            stage,
            "failed",
            message=f"{STAGE_LABELS[stage]} failed: {detail}",
            error=detail,
            diagnostics=diagnostics,
        )


def stages_from(stage: str) -> tuple[str, ...]:
    try:
        return PIPELINE_STAGES[PIPELINE_STAGES.index(stage) :]
    except ValueError as exc:
        raise ValueError(f"unknown pipeline stage: {stage}") from exc
