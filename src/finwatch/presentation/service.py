"""Build structured RipplX views exclusively from persisted, verified data."""

from __future__ import annotations

import json

from finwatch.db.repositories import Computation, Filing, Holding, Repo
from finwatch.metrics.envelope import MetricResult
from finwatch.pipeline.progress import PIPELINE_STAGES, STAGE_LABELS
from finwatch.presentation.formatting import format_metric_value
from finwatch.presentation.models import (
    BriefPeriodView,
    BriefPortfolioView,
    BriefView,
    ChannelView,
    FilingDetailView,
    FilingItemView,
    HoldingsView,
    HoldingView,
    IssuerMetricsView,
    MaterialItemView,
    MetricRowView,
    MetricsView,
    PipelineStageView,
    RedFlagView,
    ThesisImpactView,
    VerificationCheckView,
    VerificationView,
    WhatChangedView,
)
from finwatch.presentation.projection import (
    FilingProjection,
    evidence_snippet,
    has_impact,
    in_window,
    load_filing_projection,
)

STARTER_METRICS = (
    "revenue_growth",
    "net_income_trend",
    "cfo_trend",
    "liquidity_basics",
    "share_count_change",
    "simple_leverage",
)
METRIC_LABELS = {
    "revenue_growth": "Revenue growth",
    "net_income_trend": "Net income trend",
    "cfo_trend": "Operating cash flow",
    "liquidity_basics": "Liquidity",
    "share_count_change": "Share count Δ",
    "simple_leverage": "Leverage",
}
CHANNEL_LABELS = {
    "C1": "revenue",
    "C2": "margins",
    "C3": "capital structure",
    "C4": "cash/working capital",
    "C5": "competitive position",
    "C6": "governance",
    "C7": "cross-holding spillover",
}
FLAG_LABELS = {
    "item_1_03_bankruptcy": "Bankruptcy",
    "item_3_01_delisting": "Delisting",
    "item_2_04_acceleration": "Debt acceleration",
    "item_4_02_non_reliance": "Non-reliance on prior financials",
    "going_concern": "Going-concern doubt",
    "auditor_resignation": "Auditor resignation",
    "material_weakness_with_restatement_risk": "Material weakness",
    "cyber_1_05_critical_tier": "Critical cyber incident",
}
def _date(value: str | None) -> str:
    return (value or "")[:10]


def _severity(value: str) -> str:
    normalized = value.upper()
    return normalized if normalized in {"CRITICAL", "HIGH", "MEDIUM", "LOW"} else "LOW"


def _edgar_url(filing: Filing) -> str:
    if filing.primary_doc_url:
        return filing.primary_doc_url
    accession = filing.accession_number
    cik = str(int(filing.cik)) if filing.cik.isdigit() else filing.cik
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
        f"{accession}-index.htm"
    )


def _risk_changes(view: FilingProjection) -> str | None:
    findings = view.p1.risk_factor_findings if view.p1 else None
    if not findings:
        return None
    parts = []
    if findings.added:
        parts.append(f"{len(findings.added)} added")
    if findings.removed:
        parts.append(f"{len(findings.removed)} removed")
    if findings.modified:
        parts.append(f"{len(findings.modified)} modified")
    return ", ".join(parts) or None


class PresentationService:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def _views(self, since: str | None = None, until: str | None = None) -> list[FilingProjection]:
        filings = [f for f in self.repo.list_filings() if in_window(f, since, until)]
        filings.sort(key=lambda row: (row.filed_at, row.accession_number), reverse=True)
        return [load_filing_projection(self.repo, filing) for filing in filings]

    def _filing_item(self, view: FilingProjection) -> FilingItemView:
        p1 = view.p1
        flags = []
        if p1:
            for flag in p1.red_flags:
                code = flag.flag
                flags.append(
                    RedFlagView(
                        code=code,
                        label=FLAG_LABELS.get(code, code.replace("_", " ").title()),
                        severity=_severity(flag.severity),
                        edgar_url=_edgar_url(view.filing),
                        quote=evidence_snippet(view, flag.claim_ids),
                    )
                )
        return FilingItemView(
            accession=view.filing.accession_number,
            ticker=view.ticker,
            owned=bool(view.holding and view.holding.owned),
            form=view.filing.form_type,
            filed=_date(view.filing.filed_at),
            severity=_severity(view.severity),
            watch_label=None
            if view.holding and view.holding.owned
            else "watch — company-level filing read",
            material_items=[
                MaterialItemView(headline=item.headline, event_type=item.event_type)
                for item in (p1.material_items if p1 else [])
            ],
            flags=flags,
            manual_review=view.manual_review,
        )

    def _what_changed(self, view: FilingProjection) -> list[WhatChangedView]:
        if not view.p2:
            return []
        rows = []
        for record in view.p2.records_affected:
            if record.impact_class == "no_impact":
                continue
            channels = []
            for key, label in CHANNEL_LABELS.items():
                channel = record.channels.get(key)
                if not isinstance(channel, dict):
                    continue
                direction = channel.get("direction")
                if direction in (None, "not_implicated", "neutral"):
                    continue
                channels.append(
                    ChannelView(
                        label=label,
                        direction=direction,
                        magnitude=channel.get("magnitude"),
                    )
                )
            rows.append(
                WhatChangedView(
                    ticker=record.ticker,
                    impact_class=record.impact_class,
                    via=f"{view.ticker} {view.filing.form_type} {_date(view.filing.filed_at)}",
                    net_read=record.net_read.text,
                    channels=channels,
                    guidance=record.guidance_direction,
                    liquidity=record.liquidity_read,
                    net=record.net_direction,
                    risk_factor_changes=_risk_changes(view),
                )
            )
        return rows

    def _thesis(self, view: FilingProjection) -> list[ThesisImpactView]:
        if not view.p2:
            return []
        rows = []
        for record in view.p2.records_affected:
            if not record.owned or record.impact_class == "no_impact":
                continue
            holding = self.repo.get_company_by_ticker(record.ticker)
            tracked = self.repo.get_holding_by_cik(holding.cik) if holding else None
            rows.append(
                ThesisImpactView(
                    ticker=record.ticker,
                    verdict=record.thesis_check.verdict,
                    no_thesis=tracked is None or not tracked.thesis,
                )
            )
        return rows

    def _metric_rows(self, computations: list[Computation], show_all: bool) -> list[MetricRowView]:
        by_name = {
            row.tool: MetricResult.model_validate_json(row.result_json) for row in computations
        }
        names = (
            sorted(by_name) if show_all else [name for name in STARTER_METRICS if name in by_name]
        )
        result = []
        for name in names:
            metric = by_name[name]
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
                    metric=METRIC_LABELS.get(
                        metric.metric, metric.metric.replace("_", " ").title()
                    ),
                    value=value,
                    formula=metric.formula_version,
                    state=metric.status.value,
                    state_label=state_label,
                )
            )
        return result

    def _issuer_metrics(self, holding: Holding, *, show_all: bool = False) -> IssuerMetricsView:
        computations = self.repo.latest_computations(holding.ticker)
        rows = self._metric_rows(computations, show_all)
        empty = (
            None
            if any(row.state == "computed" for row in rows)
            else ("no verified financials yet (XBRL facts insufficient or not yet ingested).")
        )
        return IssuerMetricsView(
            ticker=holding.ticker, owned=bool(holding.owned), rows=rows, empty=empty
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
        analyzed = [view for view in views if view.p1]
        critical = [view for view in analyzed if view.is_critical]
        impactful = [view for view in analyzed if has_impact(view)]
        manual = [view for view in analyzed if view.manual_review]
        boring = [
            view
            for view in analyzed
            if not view.manual_review and not view.is_critical and not has_impact(view)
        ]
        owned = sorted(holding.ticker for holding in holdings if holding.owned)
        watching = sorted(holding.ticker for holding in holdings if not holding.owned)
        answer_posture = None
        if manual:
            answer = "One filing needs manual review before conclusions are shown."
            answer_posture = "risk_review"
        elif any(view.is_critical and view.holding and view.holding.owned for view in analyzed):
            answer = "One holding needs a critical review."
            answer_posture = "critical_review"
        elif any(view.is_critical for view in analyzed):
            answer = "One watched company needs attention."
            answer_posture = "risk_review"
        elif analyzed:
            answer = f"Nothing important changed. {len(boring)} routine filings reviewed."
            answer_posture = "monitor"
        elif holdings:
            answer = "No material findings yet — Sync filings or Run analysis."
        else:
            answer = "Add a holding or watch a company to start your brief."
        boring_line = None
        if boring:
            listing = ", ".join(f"{view.ticker} {view.filing.form_type}" for view in boring)
            boring_line = f"{len(boring)} routine filing(s) with no material findings ({listing})."
        questions = []
        for view in analyzed:
            questions.extend(f"{view.ticker}: {gap}" for gap in view.p1.gaps)
            questions.extend(
                f"{view.ticker}: data-quality check {check_id} — {detail}"
                for check_id, detail in view.data_quality
            )
            if view.manual_review:
                questions.append(
                    f"{view.ticker}: automated verification failed — manual review required"
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
            critical_red_flags=[self._filing_item(view) for view in critical],
            what_changed=[row for view in impactful for row in self._what_changed(view)],
            thesis_impact=[row for view in impactful for row in self._thesis(view)],
            verified_numbers=[self._issuer_metrics(h) for h in holdings if h.owned],
            open_questions=questions,
            boring_filings=boring_line,
            tracked_but_unanalyzed=bool(holdings and not analyzed),
            sample_data=sample_data,
        )

    def filing(self, accession: str) -> FilingDetailView | None:
        filing = self.repo.get_filing(accession)
        if not filing:
            return None
        view = load_filing_projection(self.repo, filing)
        holding = view.holding
        verification = None
        p1_analysis = self.repo.latest_analysis(accession, "P1")
        if p1_analysis:
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
                            detail=c.detail,
                        )
                        for c in checks
                    ],
                )
        insufficient_reason = None
        if view.p1 and view.p1.extraction_confidence == "low" and view.p1.gaps:
            insufficient_reason = "; ".join(view.p1.gaps)
        stored_stages = {row.stage: row for row in self.repo.list_filing_stages(accession)}
        inferred = {
            "download": "completed" if filing.status != "fetched" else "pending",
            "parse": "completed" if self.repo.list_filing_sections(accession) else "pending",
            "extract": "completed" if view.p1 else "pending",
            "metrics": "completed"
            if self.repo.latest_computations(view.ticker)
            else "pending",
            "impact": "completed" if view.p2 else "skipped" if view.p1 else "pending",
            "verify": "completed" if verification else "pending",
        }
        pipeline = []
        for stage in PIPELINE_STAGES:
            stored = stored_stages.get(stage)
            diagnostics = {}
            if stored:
                try:
                    diagnostics = json.loads(stored.diagnostics_json)
                except json.JSONDecodeError:
                    diagnostics = {"raw": stored.diagnostics_json}
            pipeline.append(
                PipelineStageView(
                    stage=stage,
                    label=STAGE_LABELS[stage],
                    status=stored.status if stored else inferred[stage],
                    attempts=stored.attempts if stored else 0,
                    error=stored.error if stored else None,
                    diagnostics=diagnostics,
                )
            )
        return FilingDetailView(
            filing=self._filing_item(view),
            what_changed=self._what_changed(view),
            thesis_impact=self._thesis(view),
            verified_numbers=self._issuer_metrics(holding) if holding else None,
            verification=verification,
            insufficient_reason=insufficient_reason,
            pipeline=pipeline,
        )

    def holdings(self) -> HoldingsView:
        result = []
        for holding in self.repo.list_holdings():
            filings = self.repo.list_filings(holding.cik)
            latest = filings[0] if filings else None
            severity = None
            if latest:
                view = load_filing_projection(self.repo, latest)
                severity = _severity(view.severity) if view.p1 else None
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
                        row.value.split(" ·")[0].replace("net debt/EBITDA ", "")
                        for row in computed
                        if row.metric == "Leverage"
                    ),
                    None,
                )
                parts = []
                if revenue:
                    parts.append(f"Rev {revenue}")
                if leverage:
                    parts.append(f"Leverage {leverage}")
                parts.append(f"✓{len(computed)}/{len(metrics.rows)}")
                compressed = " · ".join(parts)
            result.append(
                HoldingView(
                    ticker=holding.ticker,
                    cik=holding.cik,
                    owned=bool(holding.owned),
                    shares=holding.shares,
                    cost_basis=holding.cost_basis,
                    target_weight_pct=holding.target_weight_pct,
                    horizon=holding.horizon,
                    thesis=holding.thesis,
                    severity=severity,
                    last_filing=_date(latest.filed_at) if latest else None,
                    compressed_verified_read=compressed,
                )
            )
        owned = sorted((row for row in result if row.owned), key=lambda row: row.ticker)
        watching = sorted((row for row in result if not row.owned), key=lambda row: row.ticker)
        return HoldingsView(owned=owned, watching=watching)

    def metrics(self, ticker: str, *, as_of: str, show_all: bool = False) -> MetricsView | None:
        company = self.repo.get_company_by_ticker(ticker)
        if not company:
            return None
        holding = self.repo.get_holding_by_cik(company.cik)
        computations = self.repo.computations_as_of(ticker.upper(), as_of)
        rows = self._metric_rows(computations, show_all)
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
