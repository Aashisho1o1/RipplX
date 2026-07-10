"""Production pipeline runner — assembles the Orchestrator from real (or fake) clients
and drives it over ingested filings.

`finwatch ingest` only indexes filings + XBRL + prices; this is the step that actually
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
from finwatch.metrics.formulas import PriceProvider
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import (
    FilingAnalysis,
    Orchestrator,
    assemble_verify_bundle,
)
from finwatch.pipeline.progress import ProgressCallback, StageReporter, stages_from
from finwatch.preprocess.forms import base_form
from finwatch.preprocess.preprocessor import Preprocessor, route_sections
from finwatch.verify.checks import VerificationReport, run_all
from finwatch.verify.orchestrator import (
    fact_values_from_repo,
    persist_report,
    section_texts_from_repo,
)

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
    price_provider: PriceProvider | None = None,
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
    metrics = MetricsService(repo, price_provider, companyfacts_provider, now_fn=now_fn)
    return Orchestrator(
        repo, Preprocessor(repo, now_fn=now_fn),
        P1Extractor(llm_extract, repo, model_label=model_extract, now_fn=now_fn),
        P2Explainer(llm_reason, repo, model_label=model_reason, now_fn=now_fn),
        metrics, companyfacts_provider=companyfacts_provider, now_fn=now_fn)


def holding_records(repo: Repo) -> list[dict]:
    """The P2 `records` list (owned + watch), from the tracked holdings."""
    return [
        {"ticker": h.ticker, "owned": bool(h.owned), "shares": h.shares,
         "cost_basis": h.cost_basis, "target_weight_pct": h.target_weight_pct,
         "thesis": h.thesis}
        for h in repo.list_holdings()
    ]


# A filing is "done" only once the pipeline completed and the verifier ran. Anything else —
# never started ('fetched'/'sectioned') or errored mid-pipeline ('failed') — is retried, so a
# transient P2/network error does not permanently strand a half-analyzed filing (its P1 is
# committed before later stages run, so a P1-present check would wrongly skip it forever).
# Manual-review (``analyzed``) filings remain retryable; only a verified filing is done.
_DONE_STATUS = frozenset({"verified"})
_ANALYZABLE_FORMS = frozenset({"10-K", "10-Q", "8-K"})


def unanalyzed_filings(
    repo: Repo, cik: str | None = None, forms: frozenset[str] | None = None
) -> list[Filing]:
    """Supported filings not yet analyzed + verified (oldest first for CLI backfills).

    ``forms`` optionally narrows the queue to specific base form types (e.g.
    ``{"10-Q"}``). Matching is on ``base_form`` — case-insensitive and amendment-aware,
    so requesting ``8-K`` also selects an ``8-K/A``. ``None`` = every analyzable form
    (unchanged behavior). The ``_ANALYZABLE_FORMS`` gate still applies, so an
    unsupported request narrows to nothing rather than admitting new forms.
    """
    wanted = None if forms is None else {f.strip().upper() for f in forms}
    todo = [
        filing
        for filing in repo.list_filings(cik)
        if filing.status not in _DONE_STATUS
        and base_form(filing.form_type) in _ANALYZABLE_FORMS
        and (wanted is None or base_form(filing.form_type) in wanted)
    ]
    todo.sort(key=lambda f: (f.filed_at, f.accession_number))
    return todo


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


def process_tracked(
    repo: Repo,
    orch: Orchestrator,
    fetch_html: HtmlFetcher,
    *,
    cik: str | None = None,
    limit: int | None = None,
    now_fn: Callable[[], str] = _now_iso,
) -> list[ProcessResult]:
    """Run the pipeline over every not-yet-analyzed filing (optionally one CIK / capped)."""
    records = holding_records(repo)
    todo = unanalyzed_filings(repo, cik)
    if limit is not None:
        todo = todo[:limit]
    return [process_filing(orch, repo, f, fetch_html=fetch_html, records=records,
                           now_fn=now_fn)
            for f in todo]


def reverify(
    repo: Repo,
    accession: str,
    *,
    created_at: str | None = None,
) -> VerificationReport | None:
    """Re-run the deterministic verifier (V1/V4/V5) on a STORED analysis, purely from the
    DB — no LLM, no network. Rebuilds the bundle from the persisted P1/P2 output, the
    filing sections, and the XBRL fact values, then persists the fresh report. Returns
    None if the accession has no stored P1 analysis."""
    from finwatch.llm.schemas import P1Output, P2Output
    from finwatch.metrics.envelope import MetricsBundle

    p1a = repo.latest_analysis(accession, "P1")
    if p1a is None:
        return None
    p1 = P1Output.model_validate_json(p1a.output_json)
    p2a = repo.latest_analysis(accession, "P2")
    p2 = P2Output.model_validate_json(p2a.output_json) if p2a else None
    filing = repo.get_filing(accession)
    cik = filing.cik if filing else p1.accession_number
    bundle = assemble_verify_bundle(
        p1, p2, MetricsBundle(),
        section_texts_from_repo(repo, accession),
        fact_values_from_repo(repo, cik))
    report = run_all(bundle)
    persist_report(repo, p1a.id, report, created_at=created_at or _now_iso())
    return report
