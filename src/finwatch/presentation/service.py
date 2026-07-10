"""Build launch views exclusively from persisted, deterministically verified data."""

from __future__ import annotations

from finwatch.db.repositories import Computation, Holding, Repo
from finwatch.metrics.catalog import STARTER_METRIC_LABELS, STARTER_METRICS
from finwatch.metrics.envelope import MetricResult
from finwatch.pipeline.progress import PIPELINE_STAGES, STAGE_LABELS
from finwatch.preprocess.forms import base_form
from finwatch.presentation.canonical import build_filing_entry
from finwatch.presentation.formatting import format_metric_value
from finwatch.presentation.models import (
    BriefPeriodView,
    BriefPortfolioView,
    BriefView,
    FilingDetailView,
    HoldingsView,
    HoldingView,
    IssuerMetricsView,
    MetricRowView,
    MetricsView,
    PipelineStageView,
    VerificationCheckView,
    VerificationView,
)
from finwatch.presentation.projection import FilingProjection, in_window, load_filing_projection


def _date(value: str | None) -> str:
    return (value or "")[:10]


class PresentationService:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def _views(self, since: str | None = None, until: str | None = None) -> list[FilingProjection]:
        tracked_ciks = set(self.repo.list_tracked_ciks())
        filings = [
            filing
            for filing in self.repo.list_filings()
            if filing.cik in tracked_ciks
            and base_form(filing.form_type) in {"10-K", "10-Q", "8-K"}
            and in_window(filing, since, until)
        ]
        filings.sort(key=lambda row: (row.filed_at, row.accession_number), reverse=True)
        return [load_filing_projection(self.repo, filing) for filing in filings]

    def _metric_rows(self, computations: list[Computation]) -> list[MetricRowView]:
        by_name: dict[str, tuple[Computation, MetricResult]] = {}
        for row in computations:
            if row.id is None or row.tool not in STARTER_METRICS:
                continue
            try:
                metric = MetricResult.model_validate_json(row.result_json)
            except Exception:  # noqa: BLE001 - corrupt persisted metrics are withheld
                continue
            if (
                metric.metric != row.tool
                or metric.status.value != row.status
                or metric.formula_version != row.formula_version
                or metric.as_of != row.as_of
            ):
                continue
            if metric.status.value == "computed":
                # A computation ID is not enough provenance by itself. Every
                # rendered computed starter metric must retain typed SEC leaves.
                if not metric.inputs_used or any(
                    source.value is None
                    or not source.taxonomy
                    or not source.tag
                    or not source.unit_ref
                    or not source.accession_number
                    or not (source.instant or source.period_end)
                    for source in metric.inputs_used
                ):
                    continue
            by_name[row.tool] = (row, metric)

        result: list[MetricRowView] = []
        for name in STARTER_METRICS:
            pair = by_name.get(name)
            if pair is None:
                continue
            computation, metric = pair
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
                )
            )
        return result

    def _issuer_metrics(self, holding: Holding, *, as_of: str | None = None) -> IssuerMetricsView:
        computations = (
            self.repo.computations_as_of(holding.ticker, as_of)
            if as_of
            else self.repo.latest_computations(holding.ticker)
        )
        rows = self._metric_rows(computations)
        empty = (
            None
            if any(row.state == "computed" for row in rows)
            else "no verified financials yet (XBRL facts insufficient or not yet ingested)."
        )
        return IssuerMetricsView(
            ticker=holding.ticker,
            owned=bool(holding.owned),
            rows=rows,
            empty=empty,
        )

    def brief(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        sample_data: bool = False,
    ) -> BriefView:
        holdings = self.repo.list_holdings()
        views = self._views(since, until)
        analyzed = [view for view in views if view.analysis_present]
        entries = [build_filing_entry(self.repo, view) for view in views]
        published = [entry for entry in entries if entry.findings and not entry.manual_review]
        withheld = [entry for entry in entries if entry.manual_review]
        boring = [
            entry
            for view, entry in zip(views, entries, strict=True)
            if view.analysis_present and not entry.manual_review and not entry.findings
        ]

        owned = sorted(holding.ticker for holding in holdings if holding.owned)
        watching = sorted(holding.ticker for holding in holdings if not holding.owned)
        owned_set = set(owned)
        severe_owned = any(
            entry.ticker in owned_set
            and any(finding.severity in {"CRITICAL", "HIGH"} for finding in entry.findings)
            for entry in published
        )
        severe_watched = any(
            entry.ticker not in owned_set
            and any(finding.severity in {"CRITICAL", "HIGH"} for finding in entry.findings)
            for entry in published
        )

        answer_posture = None
        if withheld:
            count = len(withheld)
            answer = f"{count} filing{'s' if count != 1 else ''} withheld pending manual review."
            answer_posture = "risk_review"
        elif severe_owned:
            answer = "One holding needs a critical review."
            answer_posture = "critical_review"
        elif severe_watched:
            answer = "One watched company needs attention."
            answer_posture = "risk_review"
        elif published:
            answer = f"Important changes found in {len(published)} filing(s)."
            answer_posture = "risk_review"
        elif analyzed:
            answer = f"Nothing important changed. {len(boring)} routine filings reviewed."
            answer_posture = "monitor"
        elif holdings:
            answer = "No material findings yet — sync filings or run analysis."
        else:
            answer = "Add a ticker to start your brief."

        boring_line = None
        if boring:
            listing = ", ".join(f"{entry.ticker} {entry.form}" for entry in boring)
            boring_line = f"{len(boring)} routine filing(s) with no material findings ({listing})."

        questions = [
            f"{view.ticker}: a deterministic data-quality check needs review."
            for view in analyzed
            if view.data_quality
        ]
        questions.extend(
            f"{entry.ticker}: automated verification withheld this filing."
            for entry in withheld
        )
        return BriefView(
            period=BriefPeriodView(
                covered=f"{since or 'inception'} → {until or 'now'}",
                filings_in_window=len(views),
                analyzed_filings=len(analyzed),
            ),
            portfolio=BriefPortfolioView(owned=owned, watching=watching),
            answer=answer,
            answer_posture=answer_posture,
            filings=published,
            verified_numbers=[self._issuer_metrics(h) for h in holdings if h.owned],
            open_questions=questions,
            boring_filings=boring_line,
            withheld_filings=withheld,
            tracked_but_unanalyzed=bool(holdings and not analyzed),
            sample_data=sample_data,
        )

    def filing(self, accession: str) -> FilingDetailView | None:
        filing = self.repo.get_filing(accession)
        if not filing:
            return None
        view = load_filing_projection(self.repo, filing)
        entry = build_filing_entry(self.repo, view)
        holding = view.holding
        verification = None
        p1_analysis = self.repo.latest_analysis(accession, "P1")
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
            pipeline.append(
                PipelineStageView(
                    stage=stage,
                    label=STAGE_LABELS[stage],
                    status=stored.status if stored else inferred[stage],
                    attempts=stored.attempts if stored else 0,
                    error="Stage failed; details are withheld." if raw_error else None,
                    diagnostics={},
                )
            )
        return FilingDetailView(
            filing=entry,
            verified_numbers=(
                self._issuer_metrics(holding, as_of=_date(filing.filed_at)) if holding else None
            ),
            verification=verification,
            withheld_reason=entry.withheld_reason,
            pipeline=pipeline,
        )

    def holdings(self) -> HoldingsView:
        result = []
        for holding in self.repo.list_holdings():
            filings = self.repo.list_filings(holding.cik)
            latest = filings[0] if filings else None
            metrics = self._issuer_metrics(holding)
            computed = [row for row in metrics.rows if row.state == "computed"]
            compressed = None
            if computed:
                revenue = next(
                    (
                        row.value.split(" YoY")[0]
                        for row in computed
                        if row.metric == "Revenue growth"
                    ),
                    None,
                )
                leverage = next(
                    (
                        row.value.split(" ·")[0].removeprefix(
                            "net debt / (operating income + D&A) proxy "
                        )
                        for row in computed
                        if row.metric == STARTER_METRIC_LABELS["simple_leverage"]
                    ),
                    None,
                )
                parts = []
                if revenue:
                    parts.append(f"Rev {revenue}")
                if leverage:
                    parts.append(f"Leverage proxy {leverage}")
                parts.append(f"✓{len(computed)}/{len(metrics.rows)}")
                compressed = " · ".join(parts)
            result.append(
                HoldingView(
                    ticker=holding.ticker,
                    cik=holding.cik,
                    owned=bool(holding.owned),
                    last_filing=_date(latest.filed_at) if latest else None,
                    compressed_verified_read=compressed,
                )
            )
        owned = sorted((row for row in result if row.owned), key=lambda row: row.ticker)
        watching = sorted((row for row in result if not row.owned), key=lambda row: row.ticker)
        return HoldingsView(owned=owned, watching=watching)

    def metrics(self, ticker: str, *, as_of: str) -> MetricsView | None:
        company = self.repo.get_company_by_ticker(ticker)
        if not company:
            return None
        holding = self.repo.get_holding_by_cik(company.cik)
        rows = self._metric_rows(self.repo.computations_as_of(ticker.upper(), as_of))
        filings = self.repo.list_filings(company.cik)
        before_first = bool(filings and as_of < min(_date(f.filed_at) for f in filings))
        empty = (
            None
            if any(row.state == "computed" for row in rows)
            else (
                "No verified financials exist at this as-of date."
                if before_first
                else "no verified financials yet (XBRL facts insufficient or not yet ingested)."
            )
        )
        return MetricsView(
            ticker=ticker.upper(),
            owned=bool(holding and holding.owned),
            as_of=as_of,
            rows=rows,
            empty=empty,
            before_first_filing=before_first,
        )
