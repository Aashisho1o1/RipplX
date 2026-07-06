"""Production pipeline runner — assembles the Orchestrator from real (or fake) clients
and drives it over ingested filings.

`finwatch ingest` only indexes filings + XBRL + prices; this is the step that actually
runs P0→P1→metrics→P2→verify→P3 and persists the analyses the digest renders from
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
from finwatch.preprocess.forms import base_form
from finwatch.preprocess.preprocessor import Preprocessor
from finwatch.signals.engine import SignalEngine
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
    """Wire the full production Orchestrator. P1 uses the extract model; P2 + the P3
    rationale use the reason model (SignalEngine)."""
    now_fn = now_fn or _now_iso
    metrics = MetricsService(repo, price_provider, companyfacts_provider, now_fn=now_fn)
    engine = SignalEngine(repo, llm_reason, price_provider=price_provider,
                          model_label=model_reason, now_fn=now_fn)
    return Orchestrator(
        repo, Preprocessor(repo, now_fn=now_fn),
        P1Extractor(llm_extract, repo, model_label=model_extract, now_fn=now_fn),
        P2Explainer(llm_reason, repo, model_label=model_reason, now_fn=now_fn),
        metrics, companyfacts_provider=companyfacts_provider, signal_engine=engine,
        now_fn=now_fn)


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
# transient P2/P3/network error does not permanently strand a half-analyzed filing (its P1 is
# committed before later stages run, so a P1-present check would wrongly skip it forever).
# Manual-review (``analyzed``) filings remain retryable; only a verified filing is done.
_DONE_STATUS = frozenset({"verified"})
_ANALYZABLE_FORMS = frozenset({"10-K", "10-Q", "8-K"})


def unanalyzed_filings(repo: Repo, cik: str | None = None) -> list[Filing]:
    """Supported filings not yet analyzed + verified (oldest first for CLI backfills)."""
    todo = [
        filing
        for filing in repo.list_filings(cik)
        if filing.status not in _DONE_STATUS
        and base_form(filing.form_type) in _ANALYZABLE_FORMS
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
) -> ProcessResult:
    """Fetch a filing's primary document and run the full pipeline over it, updating the
    filing's status. Fetch/pipeline errors are captured (never abort a batch)."""
    ticker = filing.cik
    company = repo.get_company(filing.cik)
    if company is not None:
        ticker = company.ticker
    if not filing.primary_doc_url:
        return ProcessResult(filing.accession_number, ticker, False,
                             error="no primary-document URL indexed")
    try:
        html = fetch_html(filing.primary_doc_url)
    except Exception as exc:                                   # noqa: BLE001 — report, don't abort
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error=f"fetch failed: {exc}")
    try:
        fa: FilingAnalysis = orch.process_html(
            filing=filing, html=html, as_of=filing.filed_at, records=records)
    except Exception as exc:                                   # noqa: BLE001
        repo.set_filing_status(filing.accession_number, "failed", processed_at=now_fn())
        return ProcessResult(filing.accession_number, ticker, False,
                             error=f"pipeline failed: {exc}")
    status = "analyzed" if fa.manual_review else "verified"
    repo.set_filing_status(filing.accession_number, status, processed_at=now_fn())
    return ProcessResult(filing.accession_number, fa.ticker, True,
                         verdict=fa.verification.verdict, manual_review=fa.manual_review)


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
