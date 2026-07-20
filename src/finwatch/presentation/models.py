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


class EvidenceView(BaseModel):
    claim_id: str = Field(min_length=1, max_length=128)
    accession: str = Field(min_length=1, max_length=32)
    section_key: str = Field(min_length=1, max_length=128)
    char_start: int
    char_end: int
    quote: str = Field(min_length=1, max_length=2_000)
    section_sha256: str = Field(min_length=64, max_length=64)
    edgar_url: str = Field(min_length=1, max_length=500)


class FindingView(BaseModel):
    finding_id: str = Field(min_length=1, max_length=128)
    headline: str = Field(min_length=1, max_length=240)
    severity: Severity
    evidence: list[EvidenceView] = Field(min_length=1, max_length=3)


class FilingDigestEntry(BaseModel):
    accession: str = Field(min_length=1, max_length=32)
    ticker: str = Field(min_length=1, max_length=16)
    form: str = Field(min_length=1, max_length=16)
    filed: str = Field(min_length=1, max_length=32)
    edgar_url: str = Field(min_length=1, max_length=500)
    findings: list[FindingView] = Field(default_factory=list, max_length=3)
    withheld: bool = False
    withheld_reason: str | None = None


class MetricRowView(BaseModel):
    metric: str
    value: str
    formula: str
    state: MetricState
    state_label: str
    source_computation_id: int
    effective_as_of: str


class IssuerMetricsView(BaseModel):
    ticker: str
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None


class BriefPeriodView(BaseModel):
    covered: str
    filings_in_window: int
    analyzed_filings: int


class BriefView(BaseModel):
    period: BriefPeriodView
    tracked_tickers: list[str] = Field(default_factory=list)
    answer: str
    answer_posture: Posture | None = None
    filings: list[FilingDigestEntry] = Field(default_factory=list)
    verified_numbers: list[IssuerMetricsView] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    boring_filings: str | None = None
    withheld_filings: list[FilingDigestEntry] = Field(default_factory=list)
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


class DroppedFindingView(BaseModel):
    finding_id: str
    error_codes: list[str] = Field(default_factory=list)


class ResearchTraceView(BaseModel):
    outcome: Literal["published", "partial", "metrics_only", "withheld"]
    terminal_reason: str
    tool_call_count: int
    tool_names: list[str] = Field(default_factory=list)
    repair_used: bool
    dropped_findings: list[DroppedFindingView] = Field(default_factory=list)


class CertificateView(BaseModel):
    schema_version: str
    certificate_sha256: str
    p1_analysis_id: int
    trace_analysis_id: int
    p1_output_sha256: str = Field(min_length=64, max_length=64)
    filing: dict
    outcome: str
    terminal_reason: str
    published_finding_ids: list[str] = Field(default_factory=list)
    dropped_findings: list[DroppedFindingView] = Field(default_factory=list)
    classification: str | None = None
    evidence: list[dict] = Field(default_factory=list)
    metrics: list[dict] = Field(default_factory=list)
    verification: list[VerificationCheckView] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    agenda: list[dict] = Field(default_factory=list)
    models: dict = Field(default_factory=dict)
    prompts: dict = Field(default_factory=dict)
    budgets: dict = Field(default_factory=dict)


class FilingDetailView(BaseModel):
    filing: FilingDigestEntry
    verified_numbers: IssuerMetricsView | None = None
    verification: VerificationView | None = None
    withheld_reason: str | None = None
    pipeline: list[PipelineStageView] = Field(default_factory=list)
    research: ResearchTraceView | None = None
    certificate_url: str | None = None
    disclaimer: str = DISCLAIMER


class CompanyRowView(BaseModel):
    ticker: str
    cik: str
    last_filing: str | None = None
    compressed_verified_read: str | None = None


class CompaniesView(BaseModel):
    companies: list[CompanyRowView] = Field(default_factory=list)


class MetricsView(BaseModel):
    ticker: str
    as_of: str
    rows: list[MetricRowView] = Field(default_factory=list)
    empty: str | None = None
    before_first_filing: bool = False
