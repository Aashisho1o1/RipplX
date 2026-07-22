"""Build launch views exclusively from persisted, deterministically verified data."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date

from finwatch.db.repositories import LOCAL_USER_ID, Company, Computation, Filing, Repo
from finwatch.metrics.catalog import (
    STARTER_METRIC_EXPRESSIONS,
    STARTER_METRIC_LABELS,
    STARTER_METRICS,
)
from finwatch.metrics.envelope import InputUsed, MetricResult
from finwatch.pipeline.progress import PIPELINE_STAGES, STAGE_LABELS
from finwatch.preprocess.forms import ANALYZABLE_FORMS, base_form
from finwatch.presentation.canonical import build_filing_entry
from finwatch.presentation.formatting import (
    compressed_metric_parts,
    format_fact_value,
    format_metric_value,
    plural_count,
)
from finwatch.presentation.models import (
    BriefPeriodView,
    BriefView,
    CertificateView,
    CompaniesView,
    CompanyRowView,
    FilingDetailView,
    FilingDigestEntry,
    IssuerMetricsView,
    MetricDerivationView,
    MetricInputView,
    MetricRowView,
    MetricsView,
    PipelineStageView,
    ResearchTraceView,
    VerificationCheckView,
    VerificationView,
)
from finwatch.presentation.projection import in_window, load_filing_projection


def _date(value: str | None) -> str:
    return (value or "")[:10]


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_NO_METRIC_ROWS = "No SEC XBRL metric has been computed for this issuer yet."
_WITHHELD_METRIC_LABEL = "Withheld — the stored result failed provenance re-validation"
_METRIC_STATE_WORDS = {
    "computed": "computed",
    "unavailable": "unavailable",
    "not_applicable": "not applicable",
    "withheld": "withheld",
}
_DATA_QUALITY_CHECK = re.compile(r"V2[a-z]?")
_DETAIL_DISALLOWED = re.compile(r"[^0-9A-Za-z Δ_.,;:/()+\-=%]")
_DETAIL_MAX_CHARS = 200


def _human_date(value: str | None) -> str:
    raw = _date(value)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return raw or "unknown date"
    return f"{parsed.day} {_MONTHS[parsed.month - 1]} {parsed.year}"


def _window_label(since: str | None, until: str | None) -> str:
    start = _human_date(since) if since else "inception"
    end = _human_date(until) if until else "today"
    return f"{start} → {end}"


def _metric_summary(rows: list[MetricRowView]) -> str:
    counts = Counter(row.state for row in rows)
    parts = [
        f"{counts[state]} {word}"
        for state, word in _METRIC_STATE_WORDS.items()
        if counts[state]
    ]
    return f"{' · '.join(parts)} of {len(STARTER_METRICS)} starter metrics"


def _metric_input_view(source: InputUsed) -> MetricInputView:
    if source.instant:
        period = f"as of {source.instant}"
    elif source.period_start and source.period_end:
        period = f"{source.period_start} to {source.period_end}"
    elif source.period_end:
        period = f"period ended {source.period_end}"
    else:
        period = "period not stated"
    return MetricInputView(
        concept=source.tag,
        taxonomy=source.taxonomy,
        value=format_fact_value(source.value, source.unit_ref),
        unit=source.unit_ref or "unit not stated",
        period=period,
        accession=source.accession_number or "not stated",
    )


def _check_detail(check_id: str, detail: str | None) -> str | None:
    """Project sanitized deterministic V2 detail; gated-check details stay private."""
    if not detail or not _DATA_QUALITY_CHECK.fullmatch(check_id):
        return None
    cleaned = " ".join(_DETAIL_DISALLOWED.sub(" ", detail).split())
    return cleaned[:_DETAIL_MAX_CHARS] or None


def _coverage_sentence(
    gate_withheld: list[FilingDigestEntry],
    pipeline_failed: list[FilingDigestEntry],
) -> str:
    """State incomplete coverage without conflating gate and pipeline failures."""
    parts = []
    if gate_withheld:
        parts.append(
            f"{plural_count(len(gate_withheld), 'filing')} withheld — could not be verified"
        )
    if pipeline_failed:
        parts.append(
            f"{plural_count(len(pipeline_failed), 'filing')} could not be analyzed — "
            "the pipeline did not complete"
        )
    return f"Coverage is incomplete: {'; '.join(parts)}."


class PresentationService:
    def __init__(self, repo: Repo, *, user_id: str = LOCAL_USER_ID) -> None:
        self.repo = repo
        self.user_id = user_id

    def _scoped_filings(
        self, since: str | None = None, until: str | None = None
    ) -> list[Filing]:
        tracked_ciks = set(self.repo.list_tracked_ciks(self.user_id))
        filings = [
            filing
            for filing in self.repo.list_filings()
            if filing.cik in tracked_ciks
            and base_form(filing.form_type) in ANALYZABLE_FORMS
            and in_window(filing, since, until)
        ]
        filings.sort(key=lambda row: (row.filed_at, row.accession_number), reverse=True)
        return filings

    def _validated_metric(self, row: Computation) -> MetricResult | None:
        """Return a persisted metric only while its envelope proves provenance."""
        try:
            metric = MetricResult.model_validate_json(row.result_json)
        except Exception:  # noqa: BLE001 - corrupt persisted metrics are withheld
            return None
        if (
            metric.metric != row.tool
            or metric.status.value != row.status
            or metric.formula_version != row.formula_version
            or metric.as_of != row.as_of
        ):
            return None
        if metric.status.value == "computed" and (
            not metric.inputs_used
            or any(
                source.value is None
                or not source.taxonomy
                or not source.tag
                or not source.unit_ref
                or not source.accession_number
                or not (source.instant or source.period_end)
                for source in metric.inputs_used
            )
        ):
            return None
        return metric

    def _validated_metrics(
        self, computations: list[Computation]
    ) -> dict[str, tuple[Computation, MetricResult | None]]:
        by_name: dict[str, tuple[Computation, MetricResult | None]] = {}
        for row in computations:
            if row.id is None or row.tool not in STARTER_METRICS:
                continue
            by_name[row.tool] = (row, self._validated_metric(row))
        return by_name

    def _metric_rows(
        self, validated: dict[str, tuple[Computation, MetricResult | None]]
    ) -> list[MetricRowView]:
        result: list[MetricRowView] = []
        for name in STARTER_METRICS:
            pair = validated.get(name)
            if pair is None:
                continue
            computation, metric = pair
            if metric is None:
                result.append(
                    MetricRowView(
                        metric=STARTER_METRIC_LABELS.get(
                            name, name.replace("_", " ").title()
                        ),
                        value="— withheld",
                        formula=computation.formula_version,
                        state="withheld",
                        state_label=_WITHHELD_METRIC_LABEL,
                        source_computation_id=computation.id,
                        effective_as_of=computation.as_of,
                        derivation=None,
                    )
                )
                continue
            if metric.status.value == "computed":
                value = format_metric_value(metric)
                state_label = "Computed from SEC XBRL facts"
            elif metric.status.value == "not_applicable":
                state_label = metric.not_applicable_reason or "Not applicable for this issuer"
                value = f"— {state_label}"
            else:
                state_label = ", ".join(metric.unavailable_missing) or "Data missing"
                value = f"— {state_label}"
            result.append(
                MetricRowView(
                    metric=STARTER_METRIC_LABELS.get(
                        metric.metric, metric.metric.replace("_", " ").title()
                    ),
                    value=value,
                    formula=metric.formula_version,
                    state=metric.status.value,
                    state_label=state_label,
                    source_computation_id=computation.id,
                    effective_as_of=metric.as_of,
                    derivation=MetricDerivationView(
                        expression=STARTER_METRIC_EXPRESSIONS[metric.metric],
                        formula_version=metric.formula_version,
                        inputs=[_metric_input_view(row) for row in metric.inputs_used],
                    ),
                )
            )
        return result

    def _issuer_metrics(self, company: Company, *, as_of: str | None = None) -> IssuerMetricsView:
        computations = (
            self.repo.computations_as_of(company.ticker, as_of)
            if as_of
            else self.repo.latest_computations(company.ticker)
        )
        rows = self._metric_rows(self._validated_metrics(computations))
        return IssuerMetricsView(
            ticker=company.ticker,
            rows=rows,
            empty=None if rows else _NO_METRIC_ROWS,
            summary=_metric_summary(rows) if rows else "",
        )

    def brief(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        sample_data: bool = False,
    ) -> BriefView:
        tracked = self.repo.list_tracked_companies(self.user_id)
        scoped = self._scoped_filings(since, until)
        all_scoped = self._scoped_filings()
        views = [load_filing_projection(self.repo, filing) for filing in scoped]
        analyzed = [view for view in views if view.analysis_present]
        entries = [build_filing_entry(self.repo, view) for view in views]
        published = [entry for entry in entries if entry.outcome == "published"]
        gate_withheld = [entry for entry in entries if entry.outcome == "withheld_gate"]
        pipeline_failed = [entry for entry in entries if entry.outcome == "pipeline_failed"]
        withheld = gate_withheld + pipeline_failed
        gate_removed = [entry for entry in entries if entry.outcome == "findings_dropped"]
        reviewed = [entry for entry in entries if entry.outcome == "no_findings"]
        cleared_gate = len(published) + len(gate_removed) + len(reviewed)

        outside_window = None
        if not scoped and all_scoped:
            newest = all_scoped[0]
            issuer = self.repo.get_company(newest.cik)
            outside_window = (
                f"{issuer.ticker if issuer else newest.cik} "
                f"{base_form(newest.form_type)} filed {_human_date(newest.filed_at)} sits "
                "outside this reading window. Widen the reading window in Settings "
                "to include it."
            )

        tracked_tickers = sorted(company.ticker for company in tracked)
        severe = any(
            any(finding.severity in {"CRITICAL", "HIGH"} for finding in entry.findings)
            for entry in published
        )

        if severe:
            answer = "A tracked company needs a critical review."
        elif published:
            answer = f"Important changes found in {plural_count(len(published), 'filing')}."
        elif gate_removed:
            answer = (
                f"Every proposed change in {plural_count(len(gate_removed), 'filing')} "
                "failed the evidence gate. Verified numbers still published."
            )
        elif reviewed:
            answer = (
                "Nothing important changed. "
                f"{plural_count(len(reviewed), 'routine filing')} reviewed."
            )
        elif gate_withheld or pipeline_failed:
            answer = _coverage_sentence(gate_withheld, pipeline_failed)
        elif outside_window:
            answer = "No tracked filing falls inside your reading window."
        elif tracked:
            answer = "No material findings yet — sync filings or run analysis."
        else:
            answer = "Add a ticker to start your brief."

        if (gate_withheld or pipeline_failed) and (
            severe or published or gate_removed or reviewed
        ):
            answer = f"{answer} {_coverage_sentence(gate_withheld, pipeline_failed)}"

        questions = [
            f"{view.ticker}: a deterministic data-quality check needs review."
            for view in analyzed
            if view.data_quality
        ]
        questions.extend(
            f"{entry.ticker}: automated verification withheld this filing."
            for entry in gate_withheld
        )
        questions.extend(
            f"{entry.ticker}: analysis did not complete, so this filing was never published."
            for entry in pipeline_failed
        )
        questions.extend(
            f"{entry.ticker}: every proposed change failed the evidence gate."
            for entry in gate_removed
        )
        return BriefView(
            period=BriefPeriodView(
                covered_label=_window_label(since, until),
                filings_in_window=len(views),
                analyzed_filings=len(analyzed),
                published_filings=cleared_gate,
                withheld_filings=len(withheld),
                filings_tracked_total=len(all_scoped),
                outside_window=outside_window,
            ),
            tracked_tickers=tracked_tickers,
            answer=answer,
            filings=published,
            gate_removed_filings=gate_removed,
            verified_numbers=[self._issuer_metrics(c) for c in tracked],
            open_questions=questions,
            reviewed_filings=reviewed,
            withheld_filings=withheld,
            tracked_but_unanalyzed=bool(tracked and not analyzed and not outside_window),
            filings_synced=len(all_scoped),
            sample_data=sample_data,
        )

    def filing(
        self, accession: str, *, sample_data: bool = False
    ) -> FilingDetailView | None:
        filing = self.repo.get_filing(accession)
        if not filing or self.repo.get_user_company(self.user_id, filing.cik) is None:
            return None
        view = load_filing_projection(self.repo, filing)
        entry = build_filing_entry(self.repo, view)
        company = view.company
        verification = None
        p1_analysis = view.p1_analysis
        if p1_analysis and p1_analysis.id is not None:
            checks = self.repo.list_verification_results(p1_analysis.id)
            if checks:
                verdict = (
                    "FAIL"
                    if any(c.verdict == "fail" and c.severity == "blocking" for c in checks)
                    else (
                        "PASS_WITH_WARNINGS" if any(c.verdict == "warn" for c in checks) else "PASS"
                    )
                )
                verification = VerificationView(
                    verdict=verdict,
                    checks=[
                        VerificationCheckView(
                            check_id=c.check_id,
                            verdict=c.verdict.upper(),
                            severity=c.severity,
                            detail=_check_detail(c.check_id, c.detail),
                        )
                        for c in checks
                    ],
                )

        stored_stages = {row.stage: row for row in self.repo.list_filing_stages(accession)}
        inferred = {
            "download": "completed" if filing.status != "fetched" else "pending",
            "parse": "completed" if self.repo.list_filing_sections(accession) else "pending",
            "extract": "completed" if view.analysis_present else "pending",
            "metrics": "completed" if self.repo.latest_computations(view.ticker) else "pending",
            "impact": "skipped" if view.analysis_present else "pending",
            "verify": "completed" if verification else "pending",
        }
        pipeline = []
        for stage in PIPELINE_STAGES:
            stored = stored_stages.get(stage)
            raw_error = stored.error if stored else None
            diagnostics = {}
            if stage == "parse" and stored:
                try:
                    persisted = json.loads(stored.diagnostics_json)
                except (TypeError, ValueError):
                    persisted = {}
                sections_found = (
                    persisted.get("sections_found") if isinstance(persisted, dict) else None
                )
                if isinstance(sections_found, list) and all(
                    isinstance(section, str) for section in sections_found
                ):
                    diagnostics = {"sections_found": sections_found}
            pipeline.append(
                PipelineStageView(
                    stage=stage,
                    label=STAGE_LABELS[stage],
                    status=stored.status if stored else inferred[stage],
                    attempts=stored.attempts if stored else 0,
                    error="Stage failed; details are withheld." if raw_error else None,
                    diagnostics=diagnostics,
                )
            )
        research = None
        trace = view.trace
        if trace is not None and trace.publication_outcome is not None:
            research = ResearchTraceView(
                outcome=trace.publication_outcome,
                terminal_reason=trace.terminal_reason or "verification_incomplete",
                tool_call_count=len(trace.tool_calls),
                tool_names=list(dict.fromkeys(row.tool for row in trace.tool_calls)),
                repair_used=trace.repair_used,
                dropped_findings=[row.model_dump() for row in trace.dropped_findings],
            )
        return FilingDetailView(
            filing=entry,
            verified_numbers=(
                self._issuer_metrics(company, as_of=_date(filing.filed_at)) if company else None
            ),
            verification=verification,
            withheld_reason=entry.withheld_reason,
            pipeline=pipeline,
            research=research,
            certificate_url=(
                f"/api/filings/{accession}/certificate"
                if research and filing.status in {"verified", "analyzed"}
                else None
            ),
            sample_data=sample_data,
        )

    def certificate(self, accession: str) -> CertificateView | None:
        filing = self.repo.get_filing(accession)
        if not filing or self.repo.get_user_company(self.user_id, filing.cik) is None:
            return None
        if filing.status not in {"verified", "analyzed"}:
            return None
        view = load_filing_projection(self.repo, filing)
        trace = view.trace
        trace_row = view.trace_analysis
        p1_row = view.p1_analysis
        if (
            trace is None
            or trace_row is None
            or trace_row.id is None
            or p1_row is None
            or p1_row.id is None
            or trace.publication_outcome is None
            or trace.terminal_reason is None
            or trace.trace_analysis_id != trace_row.id
            or trace.p1_analysis_id != p1_row.id
            or trace.p1_output_sha256 is None
        ):
            return None
        if filing.status == "verified" and (
            trace.publication_outcome == "withheld"
            or trace.verification_verdict not in {"PASS", "PASS_WITH_WARNINGS"}
        ):
            return None
        if filing.status == "analyzed" and (
            trace.publication_outcome != "withheld"
            or trace.verification_verdict != "FAIL"
        ):
            return None
        publication = trace.publication_snapshot
        classification = publication.get("classification")
        evidence = publication.get("evidence", [])
        if not isinstance(evidence, list):
            return None
        withheld = trace.publication_outcome == "withheld"
        if withheld and (
            trace.published_finding_ids
            or classification is not None
            or evidence
            or any(row.arguments for row in trace.tool_calls)
        ):
            return None
        metrics = [{
            "metric_id": metric.metric,
            "status": metric.status.value,
            "formula_version": metric.formula_version,
            "as_of": metric.as_of,
            "direction_delta": metric.direction_delta,
            "direction_slack": metric.direction_slack,
            "direction_basis": metric.direction_basis,
            "inputs": [row.model_dump(mode="json") for row in metric.inputs_used],
        } for metric in trace.metric_results]
        try:
            verification = [
                VerificationCheckView.model_validate(row)
                for row in trace.verification_snapshot
            ]
        except Exception:  # noqa: BLE001 - malformed frozen state has no certificate
            return None
        tool_calls = [
            (
                {
                    "call_id": row.call_id,
                    "tool": row.tool,
                    "result_sha256": row.result_sha256,
                }
                if withheld
                else row.model_dump(mode="json")
            )
            for row in trace.tool_calls
        ]
        payload = {
            "schema_version": "certificate.v2",
            "p1_analysis_id": trace.p1_analysis_id,
            "trace_analysis_id": trace.trace_analysis_id,
            "p1_output_sha256": trace.p1_output_sha256,
            "filing": trace.filing_snapshot,
            "outcome": trace.publication_outcome,
            "terminal_reason": trace.terminal_reason,
            "published_finding_ids": trace.published_finding_ids,
            "dropped_findings": [row.model_dump() for row in trace.dropped_findings],
            "classification": classification,
            "evidence": evidence,
            "metrics": metrics,
            "verification": [row.model_dump() for row in verification],
            "tool_calls": tool_calls,
            "agenda": [row.model_dump(mode="json") for row in trace.agenda],
            "models": {
                "generator": trace.generator_model, "skeptic": trace.skeptic_model,
            },
            "prompts": {
                "generator": trace.generator_prompt_version,
                "skeptic": trace.skeptic_prompt_version,
            },
            "budgets": {
                "generator_turns": trace.generator_turns,
                "generator_tool_calls": trace.generator_tool_calls,
                "skeptic_turns": trace.skeptic_turns,
                "skeptic_tool_calls": trace.skeptic_tool_calls,
                "tool_budget": trace.tool_budget,
                "tool_calls_used": len(trace.tool_calls),
                "repair_used": trace.repair_used,
            },
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return CertificateView(certificate_sha256=digest, **payload)

    def companies(self) -> CompaniesView:
        result = []
        for company in self.repo.list_tracked_companies(self.user_id):
            supported = [
                filing
                for filing in self.repo.list_filings(company.cik)
                if base_form(filing.form_type) in ANALYZABLE_FORMS
            ]
            latest = supported[0] if supported else None
            validated = self._validated_metrics(
                self.repo.latest_computations(company.ticker)
            )
            rows = self._metric_rows(validated)
            computed = [row for row in rows if row.state == "computed"]
            compressed = None
            if rows:
                parts = compressed_metric_parts(
                    {
                        name: metric
                        for name, (_row, metric) in validated.items()
                        if metric is not None
                    }
                )
                parts.append(f"✓{len(computed)}/{len(STARTER_METRICS)}")
                compressed = " · ".join(parts)
            result.append(
                CompanyRowView(
                    ticker=company.ticker,
                    cik=company.cik,
                    newest_supported_filing=_date(latest.filed_at) if latest else None,
                    compressed_verified_read=compressed,
                )
            )
        return CompaniesView(companies=sorted(result, key=lambda row: row.ticker))

    def metrics(self, ticker: str, *, as_of: str) -> MetricsView | None:
        company = self.repo.get_company_by_ticker(ticker)
        if not company or self.repo.get_user_company(self.user_id, company.cik) is None:
            return None
        rows = self._metric_rows(
            self._validated_metrics(self.repo.computations_as_of(ticker.upper(), as_of))
        )
        filings = self.repo.list_filings(company.cik)
        before_first = bool(filings and as_of < min(_date(f.filed_at) for f in filings))
        empty = (
            None
            if rows
            else (
                "No SEC XBRL metric existed at this as-of date."
                if before_first
                else _NO_METRIC_ROWS
            )
        )
        return MetricsView(
            ticker=ticker.upper(),
            as_of=as_of,
            rows=rows,
            empty=empty,
            summary=_metric_summary(rows) if rows else "",
            before_first_filing=before_first,
        )
