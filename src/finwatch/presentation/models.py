"""Pydantic response contracts for the local RipplX web application."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from finwatch.core.types import DISCLAIMER

Posture = Literal[
    "critical_review", "risk_review", "monitor", "positive_support", "insufficient_data"
]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
MetricState = Literal["computed", "unavailable", "not_applicable"]


class MaterialItemView(BaseModel):
    headline: str
    event_type: str


class RedFlagView(BaseModel):
    code: str
    label: str
    severity: Severity
    edgar_url: str
    quote: str | None = None


class FilingItemView(BaseModel):
    accession: str
    ticker: str
    owned: bool
    form: str
    filed: str
    severity: Severity
    watch_label: str | None = None
    material_items: list[MaterialItemView] = Field(default_factory=list)
    flags: list[RedFlagView] = Field(default_factory=list)
    manual_review: bool = False


class ChannelView(BaseModel):
    label: str
    direction: str
    magnitude: str | None = None


class WhatChangedView(BaseModel):
    ticker: str
    impact_class: str
    via: str
    net_read: str
    channels: list[ChannelView] = Field(default_factory=list)
    guidance: str
    liquidity: str
    net: str
    risk_factor_changes: str | None = None


class ThesisImpactView(BaseModel):
    ticker: str
    verdict: str
    no_thesis: bool = False


class MetricRowView(BaseModel):
    metric: str
    value: str
    formula: str
    state: MetricState
    state_label: str


class IssuerMetricsView(BaseModel):
    ticker: str
    owned: bool
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None


class BriefPeriodView(BaseModel):
    covered: str
    filings_in_window: int
    analyzed_filings: int


class BriefPortfolioView(BaseModel):
    owned: list[str] = Field(default_factory=list)
    watching: list[str] = Field(default_factory=list)


class BriefView(BaseModel):
    period: BriefPeriodView
    portfolio: BriefPortfolioView
    answer: str
    answer_posture: Posture | None = None
    critical_red_flags: list[FilingItemView] = Field(default_factory=list)
    what_changed: list[WhatChangedView] = Field(default_factory=list)
    thesis_impact: list[ThesisImpactView] = Field(default_factory=list)
    verified_numbers: list[IssuerMetricsView] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    boring_filings: str | None = None
    tracked_but_unanalyzed: bool = False
    disclaimer: str = DISCLAIMER
    sample_data: bool = False


class VerificationCheckView(BaseModel):
    check_id: str
    verdict: str
    severity: str
    detail: str | None = None


class VerificationView(BaseModel):
    verdict: Literal["PASS", "PASS_WITH_WARNINGS", "FAIL"]
    checks: list[VerificationCheckView] = Field(default_factory=list)


class PipelineStageView(BaseModel):
    stage: str
    label: str
    status: str
    attempts: int = 0
    error: str | None = None
    diagnostics: dict = Field(default_factory=dict)


class FilingDetailView(BaseModel):
    filing: FilingItemView
    what_changed: list[WhatChangedView] = Field(default_factory=list)
    thesis_impact: list[ThesisImpactView] = Field(default_factory=list)
    verified_numbers: IssuerMetricsView | None = None
    verification: VerificationView | None = None
    insufficient_reason: str | None = None
    pipeline: list[PipelineStageView] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class HoldingView(BaseModel):
    ticker: str
    cik: str
    owned: bool
    severity: Severity | None = None
    last_filing: str | None = None
    compressed_verified_read: str | None = None


class HoldingsView(BaseModel):
    owned: list[HoldingView] = Field(default_factory=list)
    watching: list[HoldingView] = Field(default_factory=list)


class MetricsView(BaseModel):
    ticker: str
    owned: bool
    as_of: str
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None
    before_first_filing: bool = False
