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
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobView] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ripplx-job")

    def start(self, kind: JobKind, work: Callable[[str, JobRegistry], bool]) -> JobView:
        with self._lock:
            if any(job.state in {"queued", "running"} for job in self._jobs.values()):
                raise JobConflictError("Another sync or analysis job is already running.")
            job = JobView(
                id=uuid.uuid4().hex,
                kind=kind,
                state="queued",
                created_at=datetime.now(UTC).isoformat(),
            )
            self._jobs[job.id] = job
        self._executor.submit(self._run, job.id, work)
        return job.model_copy(deep=True)

    def _run(self, job_id: str, work: Callable[[str, JobRegistry], bool]) -> None:
        self.set_state(job_id, "running")
        try:
            partial = work(job_id, self)
            self.set_state(job_id, "partial" if partial else "completed")
        except Exception as exc:  # noqa: BLE001 - background failures become job state
            self.fail(job_id, str(exc))

    def add_item(self, job_id: str, item: JobItem) -> None:
        with self._lock:
            self._jobs[job_id].items.append(item)

    def upsert_item(self, job_id: str, item: JobItem) -> None:
        """Update a live stage row in place so polling shows current progress."""
        with self._lock:
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

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            self._jobs[job_id].state = "failed"
            self._jobs[job_id].error = error

    def get(self, job_id: str) -> JobView | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None
