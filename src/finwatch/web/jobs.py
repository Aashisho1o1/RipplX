"""Small single-worker job registry for sync and analysis operations."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

JobKind = Literal["sync", "analysis"]
JobState = Literal["queued", "running", "completed", "partial", "failed"]
DEFAULT_MAX_JOB_HISTORY = 100
LOCAL_JOB_OWNER = "local"

_SAFE_ITEM_STATES = frozenset({"queued", "running", "completed", "skipped", "failed"})
_SAFE_VERDICTS = frozenset({"PASS", "PASS_WITH_WARNINGS", "FAIL", "PARSED"})
_STAGE_LABELS = {
    "download": "Downloading filing",
    "parse": "Preparing filing",
    "extract": "Researching important changes",
    "metrics": "Computing verified metrics",
    "verify": "Verifying evidence",
}


def _safe_message(kind: JobKind, *, state: str, stage: str | None) -> str:
    """Return only fixed text; provider and exception strings are never display data."""
    if stage in _STAGE_LABELS:
        label = _STAGE_LABELS[stage]
        return {
            "queued": f"{label} is queued.",
            "running": f"{label}…",
            "completed": f"{label} complete.",
            "skipped": f"{label} was not needed.",
            "failed": f"{label} could not be completed.",
        }.get(state, f"{label} could not be completed.")
    if kind == "sync":
        return (
            "Filings and verified metrics synced."
            if state == "completed"
            else "Filing sync could not be completed."
        )
    return (
        "Analysis completed."
        if state == "completed"
        else "Analysis could not be completed."
    )


class JobItem(BaseModel):
    key: str
    state: str
    message: str
    verdict: str | None = None
    stage: str | None = None
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
    ) -> JobView:
        with self._lock:
            if any(job.state in {"queued", "running"} for job in self._jobs.values()):
                raise JobConflictError("Another sync or analysis job is already running.")
            self._prune_terminal_locked()
            job = JobView(
                id=uuid.uuid4().hex,
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
        except Exception:  # noqa: BLE001 - discard untrusted provider/exception text
            self.fail(job_id)

    def _safe_item(self, job_id: str, item: JobItem) -> JobItem:
        kind = self._jobs[job_id].kind
        state = item.state if item.state in _SAFE_ITEM_STATES else "failed"
        stage = item.stage if item.stage in _STAGE_LABELS else None
        verdict = item.verdict if item.verdict in _SAFE_VERDICTS else None
        return item.model_copy(
            update={
                "state": state,
                "stage": stage,
                "verdict": verdict,
                "message": _safe_message(kind, state=state, stage=stage),
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
                else "Analysis could not be completed."
            )

    def get(self, job_id: str, *, owner_id: str = LOCAL_JOB_OWNER) -> JobView | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return (
                job.model_copy(deep=True)
                if job and self._owners.get(job_id) == owner_id
                else None
            )
