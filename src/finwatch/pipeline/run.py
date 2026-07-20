"""Production pipeline runner — assembles the Orchestrator from real (or fake) clients
and drives it over ingested filings.

`finwatch ingest` only indexes filings + XBRL; this is the step that actually
runs P0→P1→starter metrics→verify and persists the analysis the digest renders from
(closing the gap where the production CLI never reached the pipeline). Everything here is
dependency-injected so tests drive it with a FakeLLMClient and a fixture fetcher — no
network — while the CLI passes LiteLLM clients and EdgarClient.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from finwatch.db.repositories import Filing, Repo
from finwatch.llm.router import LLMClient
from finwatch.llm.stages import P1Extractor
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import (
    FilingAnalysis,
    Orchestrator,
)
from finwatch.pipeline.progress import ProgressCallback, StageReporter
from finwatch.preprocess.forms import base_form
from finwatch.preprocess.preprocessor import Preprocessor

CompanyFactsProvider = Callable[[str], dict]
HtmlFetcher = Callable[[str], str]      # primary_doc_url -> decoded HTML


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_orchestrator(
    repo: Repo,
    *,
    llm: LLMClient,
    skeptic_llm: LLMClient | None = None,
    companyfacts_provider: CompanyFactsProvider,
    model: str | None = None,
    skeptic_model: str | None = None,
    now_fn: Callable[[], str] | None = None,
) -> Orchestrator:
    """Wire the launch pipeline around one production extraction model.

    P2/P3 research implementations are deliberately not constructed or executed until
    evidence from launch users justifies bringing them back.
    """
    now_fn = now_fn or _now_iso
    metrics = MetricsService(repo, companyfacts_provider, now_fn=now_fn)
    return Orchestrator(
        repo, Preprocessor(repo, now_fn=now_fn),
        P1Extractor(
            llm, repo, skeptic_llm=skeptic_llm,
            model_label=model, skeptic_model_label=skeptic_model,
            now_fn=now_fn,
        ),
        metrics, now_fn=now_fn)


# Automatic launch runs never backfill within an issuer. Portfolio-wide runs inspect the
# newest supported filing for each tracked CIK, discard issuer candidates that are terminal
# or retry-exhausted, then choose the newest remaining issuer candidate. ``analyzed`` means
# verification completed but withheld output for manual review, so it is terminal too.
_TERMINAL_STATUS = frozenset({"verified", "analyzed"})
_ANALYZABLE_FORMS = frozenset({"10-K", "10-Q", "8-K"})
_MAX_PIPELINE_ATTEMPTS = 2


def newest_filing_to_analyze(
    repo: Repo,
    cik: str | None = None,
    *,
    form_type: str | None = None,
) -> Filing | None:
    """Return the newest eligible per-issuer filing in scope.

    Unsupported SEC forms are excluded before per-CIK newest selection. A terminal or
    exhausted newest filing suppresses only that issuer; it never exposes the issuer's older
    history, but it also cannot starve another tracked issuer's eligible newest filing. A
    failed filing receives at most one full retry (two persisted pipeline attempts total,
    counted from download).
    """
    selected_form = base_form(form_type) if form_type else None
    if selected_form is not None and selected_form not in _ANALYZABLE_FORMS:
        raise ValueError(f"unsupported filing form: {form_type}")

    def newest_for(issuer_cik: str) -> Filing | None:
        supported = [
            filing
            for filing in repo.list_filings(issuer_cik)
            if base_form(filing.form_type) in _ANALYZABLE_FORMS
            and (selected_form is None or base_form(filing.form_type) == selected_form)
        ]
        return (
            max(supported, key=lambda row: (row.filed_at or "", row.accession_number))
            if supported
            else None
        )

    def eligible(filing: Filing | None) -> Filing | None:
        if filing is None or filing.status in _TERMINAL_STATUS:
            return None
        attempts = max(
            (
                row.attempts
                for stage in ("download", "parse", "metrics", "extract", "verify")
                if (row := repo.get_filing_stage(filing.accession_number, stage)) is not None
            ),
            default=0,
        )
        if attempts >= _MAX_PIPELINE_ATTEMPTS:
            return None
        return filing

    if cik is not None:
        return eligible(newest_for(cik))

    issuer_candidates = [
        candidate
        for company in repo.list_tracked_companies()
        if (candidate := eligible(newest_for(company.cik))) is not None
    ]
    return (
        max(
            issuer_candidates,
            key=lambda row: (row.filed_at or "", row.accession_number),
        )
        if issuer_candidates
        else None
    )


@dataclass
class ProcessResult:
    accession: str
    ticker: str
    ok: bool
    verdict: str | None = None
    withheld: bool = False
    error: str | None = None


def process_filing(
    orch: Orchestrator,
    repo: Repo,
    filing: Filing,
    *,
    fetch_html: HtmlFetcher,
    now_fn: Callable[[], str] = _now_iso,
    on_stage: ProgressCallback | None = None,
) -> ProcessResult:
    """Fetch and run one complete fresh launch attempt over a filing.

    Parse-only and extract-only resume are intentionally absent: each billable retry
    binds one downloaded document, one set of sections, and one P1 output. Errors are
    captured rather than aborting a batch.
    """
    ticker = filing.cik
    company = repo.get_company(filing.cik)
    if company is not None:
        ticker = company.ticker
    reporter = StageReporter(repo, filing.accession_number, now_fn=now_fn, callback=on_stage)
    reporter.running("download", {"form": base_form(filing.form_type)})
    if not filing.primary_doc_url:
        reporter.failed("download", "no primary-document URL indexed")
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error="no primary-document URL indexed")
    try:
        html = fetch_html(filing.primary_doc_url)
        reporter.completed(
            "download",
            {
                "document_bytes": len(html.encode("utf-8")),
                "form": base_form(filing.form_type),
                "resumed": False,
            },
        )
    except Exception as exc:                                   # noqa: BLE001 — report, don't abort
        reporter.failed("download", exc, {"form": base_form(filing.form_type)})
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error="filing download failed")
    try:
        fa: FilingAnalysis = orch.process_html(
            filing=filing,
            html=html,
            as_of=filing.filed_at,
            resume=False,
            on_stage=on_stage,
        )
    except Exception as exc:                                   # noqa: BLE001
        running = next(
            (
                stage
                for stage in repo.list_filing_stages(filing.accession_number)
                if stage.status == "running"
            ),
            None,
        )
        if running is not None:
            reporter.failed(running.stage, exc)
        current = repo.get_filing(filing.accession_number)
        if current is None or current.status not in {"verified", "analyzed", "failed"}:
            repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error="analysis pipeline failed")
    return ProcessResult(filing.accession_number, fa.ticker, True,
                         verdict=fa.verification.verdict, withheld=fa.withheld)


def process_latest(
    repo: Repo,
    orch: Orchestrator,
    fetch_html: HtmlFetcher,
    *,
    cik: str | None = None,
    form_type: str | None = None,
    now_fn: Callable[[], str] = _now_iso,
) -> list[ProcessResult]:
    """Run exactly one newest filing in scope, or return an empty no-op result."""
    filing = newest_filing_to_analyze(repo, cik, form_type=form_type)
    if filing is None:
        return []
    return [
        process_filing(
            orch,
            repo,
            filing,
            fetch_html=fetch_html,
            now_fn=now_fn,
        )
    ]
