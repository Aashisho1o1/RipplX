"""Small durable contracts for RipplX's research and monitoring loop."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RiskStatus = Literal["stable", "watch", "elevated", "unavailable"]
RiskLens = Literal[
    "liquidity",
    "leverage",
    "cash_conversion",
    "operating_deterioration",
    "share_count",
    "concentration",
    "filing_events",
    "thesis_and_promises",
]
AttentionPriority = Literal["urgent", "this_week", "routine"]
ThesisKind = Literal["reason", "risk", "assumption", "kill_criterion", "next_evidence"]
ThesisStatus = Literal[
    "draft", "confirmed", "supported", "weakened", "broken", "unclear", "retired"
]


class EvidenceRef(BaseModel):
    kind: Literal["metric", "filing", "thesis", "promise"]
    reference_id: str = Field(min_length=1, max_length=128)
    accession: str | None = Field(default=None, max_length=32)
    section_key: str | None = Field(default=None, max_length=128)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, max_length=2_000)
    section_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class RiskRadarResult(BaseModel):
    lens: RiskLens
    status: RiskStatus
    reason_codes: list[str] = Field(min_length=1, max_length=8)
    explanation: str = Field(min_length=1, max_length=500)
    metric_ids: list[str] = Field(default_factory=list, max_length=4)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=4)
    comparison_period: str | None = Field(default=None, max_length=100)
    freshness: str | None = Field(default=None, max_length=32)


class ThesisItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(pattern=r"^t[1-9][0-9]*$", max_length=16)
    kind: ThesisKind
    text: str = Field(min_length=1, max_length=320)
    status: ThesisStatus = "draft"
    lens: RiskLens | None = None


class Thesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ThesisItem] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def unique_ids(self) -> Thesis:
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("thesis item IDs must be unique")
        return self


class CompanyProfile(BaseModel):
    ticker: str
    cik: str
    monitoring_enabled: bool = True
    notification_level: Literal["urgent", "this_week", "weekly", "off"] = "urgent"
    thesis: Thesis = Field(default_factory=Thesis)
    peer_ciks: list[str] = Field(default_factory=list, max_length=6)
    updated_at: str


class ManagementPromise(BaseModel):
    promise_id: str
    ticker: str
    accession: str
    section_key: str
    char_start: int
    char_end: int
    section_sha256: str
    quote: str = Field(min_length=1, max_length=2_000)
    target_period: str | None = None
    target_metric: str | None = None
    status: Literal["open", "met", "missed", "unclear", "retired"] = "open"


class PeerComparison(BaseModel):
    ticker: str
    name: str | None = None
    sic_code: str | None = None
    reason: str
    caveat: str = "An SEC industry code does not prove that two companies compete directly."
    risk_statuses: dict[str, RiskStatus] = Field(default_factory=dict)
    metrics: dict[str, str] = Field(default_factory=dict)


class ValuationAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discount_rate: float = Field(default=0.10, gt=0.04, lt=0.30)
    terminal_growth: float = Field(default=0.025, ge=-0.05, lt=0.08)
    conservative_growth: float = Field(default=0.00, ge=-0.50, le=1.0)
    base_growth: float = Field(default=0.05, ge=-0.50, le=1.0)
    optimistic_growth: float = Field(default=0.10, ge=-0.50, le=1.0)

    @model_validator(mode="after")
    def ordered(self) -> ValuationAssumptions:
        if not self.conservative_growth <= self.base_growth <= self.optimistic_growth:
            raise ValueError("growth scenarios must be ordered")
        if self.terminal_growth >= self.discount_rate:
            raise ValueError("terminal growth must be below the discount rate")
        return self


class ValuationScenario(BaseModel):
    name: Literal["conservative", "base", "optimistic"]
    growth: float
    implied_value_per_share: float
    change_percent: float | None = None


class ValuationRun(BaseModel):
    run_id: str
    ticker: str
    price: float = Field(gt=0)
    price_as_of: str
    status: Literal["computed", "unavailable"]
    label: Literal["Demanding", "Balanced", "Undemanding", "Unavailable"]
    explanation: str
    assumptions: ValuationAssumptions
    scenarios: list[ValuationScenario] = Field(default_factory=list, max_length=3)
    reverse_dcf_growth: float | None = None
    trailing_pe: float | None = None
    price_to_fcf: float | None = None
    fcf_yield: float | None = None
    inputs: list[EvidenceRef] = Field(default_factory=list)
    formula_version: str
    certificate_hash: str
    created_at: str


class ChangeImpact(BaseModel):
    finding_id: str
    headline: str
    driver: Literal[
        "revenue", "earnings", "cash_flow", "balance_sheet", "per_share", "operations"
    ]
    effect: Literal["upside", "downside", "mixed", "uncertain"]
    implication: str
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=3)


class StockImpactSnapshot(BaseModel):
    directional_pressure: Literal["upside", "downside", "mixed", "uncertain"]
    summary: str
    priced_in: str
    watch_next: str
    reason_codes: list[str] = Field(default_factory=list, max_length=8)
    changes: list[ChangeImpact] = Field(default_factory=list, max_length=3)
    formula_version: str = "stock_impact.v1"


class AttentionEvent(BaseModel):
    event_id: int | None = None
    event_key: str
    ticker: str
    cik: str
    accession: str | None = None
    priority: AttentionPriority
    reason_codes: list[str]
    risk_changes: list[str] = Field(default_factory=list)
    thesis_impacts: list[str] = Field(default_factory=list)
    created_at: str
    read_at: str | None = None


class BeforeYouBuyBrief(BaseModel):
    ticker: str
    cik: str
    company_name: str | None = None
    as_of: str
    attention: list[AttentionEvent] = Field(default_factory=list)
    risks: list[RiskRadarResult]
    business_summary: str | None = None
    business_evidence: EvidenceRef | None = None
    recent_filings: list[dict] = Field(default_factory=list)
    metrics: dict
    valuation: ValuationRun | None = None
    impact: StockImpactSnapshot
    profile: CompanyProfile
    thesis: Thesis
    promises: list[ManagementPromise] = Field(default_factory=list)
    peers: list[PeerComparison] = Field(default_factory=list)
    manual_peer_tickers: list[str] = Field(default_factory=list, max_length=6)
    questions: list[str] = Field(default_factory=list)
    certificate_urls: list[str] = Field(default_factory=list)
    disclaimer: str
