"""Small single-worker job registry for sync, filing analysis, and company research."""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from finwatch.pipeline.progress import FAILURE_REASON_LABELS

JobKind = Literal["sync", "analysis", "research"]
JobState = Literal["queued", "running", "completed", "partial", "failed"]
DEFAULT_MAX_JOB_HISTORY = 100
LOCAL_JOB_OWNER = "local"
# Hosted signup is public and every analysis or research job spends the operator's
# provider budget, so one account gets a small hourly allowance of model-backed jobs.
MODEL_JOBS_PER_OWNER_PER_HOUR = 10

logger = logging.getLogger("finwatch.jobs")

_SAFE_ITEM_STATES = frozenset({"queued", "running", "completed", "skipped", "failed"})
_SAFE_VERDICTS = frozenset({"PASS", "PASS_WITH_WARNINGS", "FAIL", "PARSED"})
_STAGE_LABELS = {
    "download": "Downloading filing",
    "parse": "Preparing filing",
    "extract": "Researching important changes",
    "metrics": "Computing verified metrics",
    "verify": "Verifying evidence",
}
_JOB_REASONS = {
    "no_filings_synced": "No SEC filings have been synced yet. Sync filings, then run analysis.",
    "form_not_synced": (
        "No filing of the selected type has been synced. "
        "Sync filings or choose another filing type."
    ),
    "newest_already_analyzed": (
        "The newest supported filing has already been analyzed. "
        "There is nothing new to analyze right now."
    ),
}
# No-op reasons plus finwatch's own typed stage-failure reasons. Both are closed
# vocabularies of fixed identifiers mapped to fixed operator-facing sentences; neither
# carries provider text, filing content, or model-authored prose.
_SAFE_REASONS = {**_JOB_REASONS, **FAILURE_REASON_LABELS}


def _safe_message(
    kind: JobKind, *, state: str, stage: str | None, reason: str | None = None
) -> str:
    """Return only fixed text; provider and exception strings are never display data."""
    if stage in _STAGE_LABELS:
        label = _STAGE_LABELS[stage]
        if state in {"failed", "skipped"} or state not in {"queued", "running", "completed"}:
            failed = f"{label} could not be completed."
            if state == "skipped":
                return f"{label} was not needed."
            # Name WHY the stage failed when finwatch raised a typed reason. Without it
            # every failure reads identically and an operator cannot separate a bad key
            # from unusable model output.
            explanation = FAILURE_REASON_LABELS.get(reason or "")
            return f"{failed} {explanation}" if explanation else failed
        return {
            "queued": f"{label} is queued.",
            "running": f"{label}…",
            "completed": f"{label} complete.",
        }[state]
    if reason in _SAFE_REASONS:
        return _SAFE_REASONS[reason]
    if kind == "sync":
        return (
            "Filings and verified metrics synced."
            if state == "completed"
            else "Filing sync could not be completed."
        )
    label = "Research" if kind == "research" else "Analysis"
    return f"{label} completed." if state == "completed" else f"{label} could not be completed."


class JobItem(BaseModel):
    key: str
    state: str
    message: str = ""
    verdict: str | None = None
    stage: str | None = None
    reason: str | None = None
    diagnostics: dict = Field(default_factory=dict)


class JobView(BaseModel):
    id: str
    kind: JobKind
    state: JobState
    created_at: str
    items: list[JobItem] = Field(default_factory=list)
    error: str | None = None


class JobConflictError(RuntimeError):
    pass


class JobRateLimiter:
    """Per-owner sliding-window ceiling on model-spending job starts (one process)."""

    def __init__(
        self,
        limit: int = MODEL_JOBS_PER_OWNER_PER_HOUR,
        *,
        window_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit and window_seconds must be positive")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._starts: dict[str, deque[float]] = {}

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._window
        for owner_id, starts in list(self._starts.items()):
            while starts and starts[0] <= cutoff:
                starts.popleft()
            if not starts:
                del self._starts[owner_id]

    def retry_after(self, owner_id: str) -> int | None:
        """Seconds until ``owner_id`` may start another job, or None when allowed."""
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            starts = self._starts.get(owner_id)
            if starts is None or len(starts) < self._limit:
                return None
            return max(1, math.ceil(starts[0] + self._window - now))

    def record(self, owner_id: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            self._starts.setdefault(owner_id, deque()).append(now)


class JobRegistry:
    def __init__(self, *, max_jobs: int = DEFAULT_MAX_JOB_HISTORY) -> None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        self._lock = threading.Lock()
        self._jobs: dict[str, JobView] = {}
        self._owners: dict[str, str] = {}
        self._max_jobs = max_jobs
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ripplx-job")

    def _prune_terminal_locked(self) -> None:
        while len(self._jobs) >= self._max_jobs:
            terminal_id = next(
                (
                    job_id
                    for job_id, job in self._jobs.items()
                    if job.state not in {"queued", "running"}
                ),
                None,
            )
            if terminal_id is None:
                raise JobConflictError("The active job registry is at capacity.")
            del self._jobs[terminal_id]
            del self._owners[terminal_id]

    def start(
        self,
        kind: JobKind,
        work: Callable[[str, JobRegistry], bool],
        *,
        owner_id: str = LOCAL_JOB_OWNER,
        job_id: str | None = None,
    ) -> JobView:
        with self._lock:
            if any(job.state in {"queued", "running"} for job in self._jobs.values()):
                raise JobConflictError("Another background job is already running.")
            self._prune_terminal_locked()
            job = JobView(
                id=job_id or uuid.uuid4().hex,
                kind=kind,
                state="queued",
                created_at=datetime.now(UTC).isoformat(),
            )
            self._jobs[job.id] = job
            self._owners[job.id] = owner_id
        self._executor.submit(self._run, job.id, work)
        return job.model_copy(deep=True)

    def _run(self, job_id: str, work: Callable[[str, JobRegistry], bool]) -> None:
        self.set_state(job_id, "running")
        try:
            partial = work(job_id, self)
            self.set_state(job_id, "partial" if partial else "completed")
        except Exception as exc:  # noqa: BLE001 - discard untrusted provider/exception text
            # Name the failure for the operator without its message: exception text can
            # carry provider responses, filing content, or credentials.
            logger.error(
                "job %s kind=%s failed: %s",
                job_id,
                self._jobs[job_id].kind,
                type(exc).__name__,
            )
            self.fail(job_id)

    def shutdown(self) -> None:
        """Drop queued work at process shutdown; a running job finishes its attempt."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _safe_item(self, job_id: str, item: JobItem) -> JobItem:
        kind = self._jobs[job_id].kind
        state = item.state if item.state in _SAFE_ITEM_STATES else "failed"
        stage = item.stage if item.stage in _STAGE_LABELS else None
        verdict = item.verdict if item.verdict in _SAFE_VERDICTS else None
        reason = item.reason if item.reason in _SAFE_REASONS else None
        return item.model_copy(
            update={
                "state": state,
                "stage": stage,
                "verdict": verdict,
                "reason": reason,
                "message": _safe_message(kind, state=state, stage=stage, reason=reason),
                "diagnostics": {},
            },
            deep=True,
        )

    def add_item(self, job_id: str, item: JobItem) -> None:
        with self._lock:
            self._jobs[job_id].items.append(self._safe_item(job_id, item))

    def upsert_item(self, job_id: str, item: JobItem) -> None:
        """Update a live stage row in place so polling shows current progress."""
        with self._lock:
            item = self._safe_item(job_id, item)
            items = self._jobs[job_id].items
            for index, current in enumerate(items):
                if current.key == item.key:
                    items[index] = item
                    break
            else:
                items.append(item)

    def set_state(self, job_id: str, state: JobState) -> None:
        with self._lock:
            self._jobs[job_id].state = state

    def fail(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.state = "failed"
            job.error = (
                "Filing sync could not be completed."
                if job.kind == "sync"
                else (
                    "Research could not be completed."
                    if job.kind == "research"
                    else "Analysis could not be completed."
                )
            )

    def get(self, job_id: str, *, owner_id: str = LOCAL_JOB_OWNER) -> JobView | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return (
                job.model_copy(deep=True) if job and self._owners.get(job_id) == owner_id else None
            )
