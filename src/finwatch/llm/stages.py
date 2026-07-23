"""Public filing-research stage facade around the bounded JSON harness."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from finwatch.db.repositories import Repo
from finwatch.llm.harness import FilingResearchHarness, HarnessError, HarnessResult
from finwatch.llm.router import LLMClient
from finwatch.metrics.envelope import MetricsBundle
from finwatch.verify.checks import CheckResult


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StageError(RuntimeError):
    """A run-level harness failure that withholds the filing.

    Carries the originating typed ``reason`` so the stage ledger can record WHY the
    stage failed without parsing this exception's message. The message itself is never
    persisted or displayed.
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        self.reason = reason
        super().__init__(message)


class P1Extractor:
    """Lean research harness: bounded tools, one repair, Skeptic, deterministic prune."""

    def __init__(
        self,
        llm: LLMClient,
        repo: Repo,
        *,
        skeptic_llm: LLMClient | None = None,
        model_label: str | None = None,
        skeptic_model_label: str | None = None,
        now_fn: Callable[[], str] = _now_iso,
    ) -> None:
        self.harness = FilingResearchHarness(
            llm, repo, skeptic=skeptic_llm,
            generator_model=model_label, skeptic_model=skeptic_model_label,
            now_fn=now_fn,
        )

    def run(
        self,
        *,
        filing_meta: dict,
        sections: dict,
        prior_sections: dict | None = None,
        metrics: MetricsBundle | None = None,
        data_quality: list[CheckResult] | None = None,
    ) -> HarnessResult:
        try:
            result = self.harness.run(
                filing_meta=filing_meta,
                sections=sections,
                prior_sections=prior_sections or {},
                metrics=metrics or MetricsBundle(),
                data_quality=data_quality or [],
            )
        except HarnessError as exc:
            raise StageError(
                f"P1 harness stopped: {exc.reason}", reason=exc.reason
            ) from exc
        return result
