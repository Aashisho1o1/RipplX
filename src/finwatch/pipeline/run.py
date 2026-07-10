"""Production pipeline runner — assembles the Orchestrator from real (or fake) clients
and drives it over ingested filings.

`finwatch ingest` only indexes filings + XBRL; this is the step that actually
runs P0→P1→metrics→P2→verify and persists the analyses the digest renders from
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
from finwatch.llm.stages import P1Extractor, P2Explainer
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import (
    FilingAnalysis,
    Orchestrator,
)
from finwatch.pipeline.progress import ProgressCallback, StageReporter, stages_from
from finwatch.preprocess.forms import base_form
from finwatch.preprocess.preprocessor import Preprocessor, route_sections

CompanyFactsProvider = Callable[[str], dict]
HtmlFetcher = Callable[[str], str]      # primary_doc_url -> decoded HTML


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_orchestrator(
    repo: Repo,
    *,
    llm_extract: LLMClient,
    llm_reason: LLMClient,
    companyfacts_provider: CompanyFactsProvider,
    model_extract: str | None = None,
    model_reason: str | None = None,
    now_fn: Callable[[], str] | None = None,
) -> Orchestrator:
    """Wire the launch pipeline. P1 uses the extract model and P2 uses the reason model.

    P3/shadow signals are deliberately not constructed or executed in the prototype launch
    path. The research implementation remains isolated under ``finwatch.signals`` until user
    evidence justifies bringing it back.
    """
    now_fn = now_fn or _now_iso
    metrics = MetricsService(repo, companyfacts_provider, now_fn=now_fn)
    return Orchestrator(
        repo, Preprocessor(repo, now_fn=now_fn),
        P1Extractor(llm_extract, repo, model_label=model_extract, now_fn=now_fn),
        P2Explainer(llm_reason, repo, model_label=model_reason, now_fn=now_fn),
        metrics, companyfacts_provider=companyfacts_provider, now_fn=now_fn)


def holding_records(repo: Repo) -> list[dict]:
    """Minimal P2 issuer identities; never disclose portfolio accounting or thesis data."""
    return [
        {"ticker": h.ticker, "owned": bool(h.owned)}
        for h in repo.list_holdings()
    ]


# Automatic launch runs never backfill. The newest supported filing is selected first; if
# that filing is already terminal, the run is a no-op rather than falling through to older
# history. ``analyzed`` means verification completed but withheld output for manual review,
# so it is terminal too—repeated clicks must not rebill the same failed artifact.
_TERMINAL_STATUS = frozenset({"verified", "analyzed"})
_ANALYZABLE_FORMS = frozenset({"10-K", "10-Q", "8-K"})


def newest_filing_to_analyze(repo: Repo, cik: str | None = None) -> Filing | None:
    """Return the newest supported filing in scope only when it needs analysis.

    Unsupported SEC forms are excluded before newest selection. Crucially, an already
    terminal newest filing returns ``None``; it never exposes an older filing for implicit
    historical replay.
    """
    candidates = [
        filing
        for filing in repo.list_filings(cik)
        if base_form(filing.form_type) in _ANALYZABLE_FORMS
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda f: (f.filed_at or "", f.accession_number))
    return None if newest.status in _TERMINAL_STATUS else newest


@dataclass
class ProcessResult:
    accession: str
    ticker: str
    ok: bool
    verdict: str | None = None
    manual_review: bool = False
    error: str | None = None


def process_filing(
    orch: Orchestrator,
    repo: Repo,
    filing: Filing,
    *,
    fetch_html: HtmlFetcher,
    records: list[dict],
    now_fn: Callable[[], str] = _now_iso,
    rerun_from: str | None = None,
    on_stage: ProgressCallback | None = None,
) -> ProcessResult:
    """Fetch a filing's primary document and run the full pipeline over it, updating the
    filing's status. Fetch/pipeline errors are captured (never abort a batch)."""
    ticker = filing.cik
    company = repo.get_company(filing.cik)
    if company is not None:
        ticker = company.ticker
    reporter = StageReporter(repo, filing.accession_number, now_fn=now_fn, callback=on_stage)
    if rerun_from is not None:
        if rerun_from == "extract" and not repo.list_filing_sections(filing.accession_number):
            reporter.failed("extract", "analysis rerun requires persisted parsed sections")
            repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
            return ProcessResult(
                filing.accession_number,
                ticker,
                False,
                error="analysis rerun requires persisted parsed sections",
            )
        repo.reset_filing_stages(filing.accession_number, stages_from(rerun_from))
        if rerun_from in {"download", "parse", "extract"}:
            repo.clear_filing_analysis(filing.accession_number)
    fetch_required = rerun_from != "extract"
    if fetch_required and not filing.primary_doc_url:
        reporter.failed("download", "no primary-document URL indexed")
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error="no primary-document URL indexed")
    if fetch_required:
        reporter.running("download", {"form": base_form(filing.form_type)})
    try:
        html = fetch_html(filing.primary_doc_url) if fetch_required else None
        reporter.completed(
            "download",
            {
                "document_bytes": len((html or "").encode("utf-8")),
                "form": base_form(filing.form_type),
                "resumed": not fetch_required,
            },
            message="Downloaded (reused parsed document)" if not fetch_required else None,
        )
    except Exception as exc:                                   # noqa: BLE001 — report, don't abort
        reporter.failed("download", exc, {"form": base_form(filing.form_type)})
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error=f"fetch failed: {exc}")
    try:
        fa: FilingAnalysis = orch.process_html(
            filing=filing,
            html=html,
            as_of=filing.filed_at,
            records=records,
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
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error=f"pipeline failed: {exc}")
    status = "analyzed" if fa.manual_review else "verified"
    repo.set_filing_status(filing.accession_number, status, processed_at=now_fn())
    return ProcessResult(filing.accession_number, fa.ticker, True,
                         verdict=fa.verification.verdict, manual_review=fa.manual_review)


def process_parsing(
    repo: Repo,
    filing: Filing,
    *,
    fetch_html: HtmlFetcher,
    now_fn: Callable[[], str] = _now_iso,
    on_stage: ProgressCallback | None = None,
) -> ProcessResult:
    """Rerun only download + deterministic parsing and invalidate stale analysis."""
    company = repo.get_company(filing.cik)
    ticker = company.ticker if company else filing.cik
    reporter = StageReporter(repo, filing.accession_number, now_fn=now_fn, callback=on_stage)
    repo.reset_filing_stages(filing.accession_number, stages_from("parse"))
    if not filing.primary_doc_url:
        error = "no primary-document URL indexed"
        reporter.failed("download", error)
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False, error=error)
    reporter.running("download", {"form": base_form(filing.form_type)})
    try:
        html = fetch_html(filing.primary_doc_url)
        reporter.completed(
            "download",
            {"document_bytes": len(html.encode("utf-8")), "form": base_form(filing.form_type)},
        )
        reporter.running("parse")
        if not route_sections(filing.form_type, html):
            raise ValueError(
                f"{filing.form_type} section routing produced no canonical sections"
            )
        repo.clear_filing_analysis(filing.accession_number)
        result = Preprocessor(repo, now_fn=now_fn).preprocess_html(
            accession_number=filing.accession_number,
            cik=filing.cik,
            form_type=filing.form_type,
            filed_at=filing.filed_at,
            period_of_report=filing.period_of_report,
            html=html,
        )
        if not result.sections:
            raise ValueError(
                f"{filing.form_type} section routing produced no canonical sections"
            )
        reporter.completed(
            "parse",
            {
                "detected_form": result.form_family,
                "document_bytes": len(html.encode("utf-8")),
                "sections_found": [section.section_key for section in result.sections],
                "section_count": len(result.sections),
            },
        )
    except Exception as exc:  # noqa: BLE001 - preserve parser diagnostics
        running = next(
            (
                stage
                for stage in repo.list_filing_stages(filing.accession_number)
                if stage.status == "running"
            ),
            None,
        )
        reporter.failed(running.stage if running else "parse", exc)
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False, error=str(exc))
    repo.set_filing_status(filing.accession_number, "sectioned", processed_at=now_fn())
    return ProcessResult(filing.accession_number, ticker, True, verdict="PARSED")


def process_latest(
    repo: Repo,
    orch: Orchestrator,
    fetch_html: HtmlFetcher,
    *,
    cik: str | None = None,
    now_fn: Callable[[], str] = _now_iso,
) -> list[ProcessResult]:
    """Run exactly one newest filing in scope, or return an empty no-op result."""
    records = holding_records(repo)
    filing = newest_filing_to_analyze(repo, cik)
    if filing is None:
        return []
    return [
        process_filing(
            orch,
            repo,
            filing,
            fetch_html=fetch_html,
            records=records,
            now_fn=now_fn,
        )
    ]
