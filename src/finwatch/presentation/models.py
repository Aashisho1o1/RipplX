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
Signal = Literal["STRONG_REVIEW_SELL", "TRIM", "HOLD", "ACCUMULATE"]


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
    posture: Posture | None = None
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


class ShadowSignalView(BaseModel):
    ticker: str
    signal: Signal
    posture: Posture
    rules_fired: list[str] = Field(default_factory=list)
    rationale: str | None = None
    counter_evidence: str | None = None
    what_would_change_this: list[str] = Field(default_factory=list)
    rationale_withheld: bool = False


class BriefPeriodView(BaseModel):
    covered: str
    filings_in_window: int


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
    shadow_signals: list[ShadowSignalView] = Field(default_factory=list)
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


class FilingDetailView(BaseModel):
    filing: FilingItemView
    what_changed: list[WhatChangedView] = Field(default_factory=list)
    thesis_impact: list[ThesisImpactView] = Field(default_factory=list)
    verified_numbers: IssuerMetricsView | None = None
    verification: VerificationView | None = None
    shadow_signal: ShadowSignalView | None = None
    insufficient_reason: str | None = None
    disclaimer: str = DISCLAIMER


class HoldingView(BaseModel):
    ticker: str
    cik: str
    owned: bool
    shares: float | None = None
    cost_basis: float | None = None
    target_weight_pct: float | None = None
    horizon: str | None = None
    thesis: str | None = None
    posture: Posture | None = None
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


class TrackRecordView(BaseModel):
    evaluations: int
    posture_counts: dict[Posture, int]
    signal_counts: dict[Signal, int]
    outcomes_reviewed: int
