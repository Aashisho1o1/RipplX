"""Small durable contracts for RipplX's research and monitoring loop."""

from __future__ import annotations

import hashlib
import json
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
ResearchStatus = Literal["queued", "running", "completed", "partial", "failed"]
ResearchEvidenceLabel = Literal["fact", "calculation", "unavailable"]
ResearchObligationId = Literal[
    "BUSINESS_ECONOMICS",
    "IMPORTANT_CHANGES",
    "FINANCIAL_QUALITY_AND_DOWNSIDE",
    "PEER_CONTEXT",
    "SOURCE_COVERAGE",
]
ResearchObligationState = Literal["supported", "mixed", "unavailable"]
ResearchCategory = Literal[
    "business",
    "change",
    "financial_quality",
    "peer",
]
ResearchMechanism = Literal[
    "revenue",
    "margin",
    "working_capital",
    "cash_conversion",
    "capital_spending",
    "leverage",
    "liquidity",
    "dilution",
    "uncertain",
]
ResearchScenario = Literal["downside", "upside", "mixed", "neutral"]


class EvidenceRef(BaseModel):
    kind: Literal["metric", "filing", "thesis", "promise"]
    reference_id: str = Field(min_length=1, max_length=128)
    accession: str | None = Field(default=None, max_length=32)
    section_key: str | None = Field(default=None, max_length=128)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, max_length=2_000)
    section_sha256: str | None = Field(default=None, min_length=64, max_length=64)


def research_observation_hash(
    *,
    tool: str,
    evidence_label: ResearchEvidenceLabel,
    text: str,
    evidence: list[EvidenceRef],
    metric_ids: list[str],
    as_of: str | None,
) -> str:
    """Return the content address for the exact persisted observation fields."""
    payload = {
        "tool": tool,
        "evidence_label": evidence_label,
        "text": text,
        "evidence": [row.model_dump(mode="json") for row in evidence],
        "metric_ids": metric_ids,
        "as_of": as_of,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class ResearchObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(pattern=r"^o_[0-9a-f]{16}$")
    tool: Literal[
        "search_filing_sections",
        "get_verified_changes",
        "get_financial_context",
        "get_peer_context",
    ]
    evidence_label: ResearchEvidenceLabel
    text: str = Field(min_length=1, max_length=1_200)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=6)
    metric_ids: list[str] = Field(default_factory=list, max_length=8)
    as_of: str | None = Field(default=None, max_length=40)
    stable_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def hash_matches_id(self) -> ResearchObservation:
        expected = research_observation_hash(
            tool=self.tool,
            evidence_label=self.evidence_label,
            text=self.text,
            evidence=self.evidence,
            metric_ids=self.metric_ids,
            as_of=self.as_of,
        )
        if self.stable_hash != expected or self.observation_id != f"o_{expected[:16]}":
            raise ValueError("observation content, ID, and stable hash must match")
        return self


class ResearchObligation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obligation: ResearchObligationId
    state: ResearchObligationState


class ResearchInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str = Field(pattern=r"^i[1-5]$")
    category: ResearchCategory
    headline: str = Field(min_length=1, max_length=180)
    evidence_summary: str = Field(min_length=1, max_length=320)
    driver: str = Field(min_length=1, max_length=180)
    mechanism: ResearchMechanism
    implication: str = Field(min_length=1, max_length=420)
    scenario: ResearchScenario
    assumptions: list[str] = Field(min_length=1, max_length=2)
    limitations: list[str] = Field(min_length=1, max_length=2)
    observation_ids: list[str] = Field(min_length=1, max_length=5)
    evidence_status: Literal["conditional_inference"] = "conditional_inference"


class CompanyResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["company_research.v2"] = "company_research.v2"
    ticker: str
    cik: str
    as_of: str
    data_cutoff: str
    summary: str = Field(min_length=1, max_length=600)
    obligations: list[ResearchObligation] = Field(min_length=5, max_length=5)
    insights: list[ResearchInsight] = Field(default_factory=list, max_length=5)
    observations: list[ResearchObservation] = Field(default_factory=list, max_length=24)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=6)
    disclaimer: str

    @model_validator(mode="after")
    def complete_and_unique(self) -> CompanyResearchReport:
        if {row.obligation for row in self.obligations} != set(ResearchObligationId.__args__):
            raise ValueError("research report must contain every obligation exactly once")
        if len({row.obligation for row in self.obligations}) != len(self.obligations):
            raise ValueError("research obligations must be unique")
        if len({row.insight_id for row in self.insights}) != len(self.insights):
            raise ValueError("research insight IDs must be unique")
        if len({row.observation_id for row in self.observations}) != len(self.observations):
            raise ValueError("research observation IDs must be unique")
        known = {row.observation_id for row in self.observations}
        if any(set(row.observation_ids) - known for row in self.insights):
            raise ValueError("research insight references an unknown observation")
        return self


class ResearchToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal[
        "search_filing_sections",
        "get_verified_changes",
        "get_financial_context",
        "get_peer_context",
    ]
    arguments_sha256: str = Field(min_length=64, max_length=64)
    result_sha256: str = Field(min_length=64, max_length=64)
    cached: bool


class ResearchTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["company_research_trace.v2"] = "company_research_trace.v2"
    tool_calls: list[ResearchToolCall] = Field(default_factory=list, max_length=4)
    obligation_transitions: list[ResearchObligation] = Field(min_length=5, max_length=5)
    tool_budget_used: int = Field(ge=0, le=4)
    turn_budget_used: int = Field(ge=0, le=6)
    repair_used: bool
    dropped_insights: dict[str, list[str]] = Field(default_factory=dict)
    model: str
    prompt_version: str
    compiler_version: str
    terminal_reason: str


class ResearchRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ticker: str
    cik: str
    status: ResearchStatus
    input_hash: str = Field(min_length=64, max_length=64)
    report: CompanyResearchReport | None = None
    trace: ResearchTrace | None = None
    created_at: str
    completed_at: str | None = None

    @model_validator(mode="after")
    def artifact_matches_state(self) -> ResearchRun:
        terminal = self.status in {"completed", "partial"}
        if terminal != (self.report is not None and self.trace is not None):
            raise ValueError("completed research requires one report and trace")
        if not terminal and (self.report is not None or self.trace is not None):
            raise ValueError("non-completed research cannot expose an artifact")
        return self


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


class ChangeImpact(BaseModel):
    finding_id: str
    headline: str
    driver: Literal["revenue", "earnings", "cash_flow", "balance_sheet", "per_share", "operations"]
    effect: Literal["upside", "downside", "mixed", "uncertain"]
    implication: str
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=3)


class StockImpactSnapshot(BaseModel):
    directional_pressure: Literal["upside", "downside", "mixed", "uncertain"]
    summary: str
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
    impact: StockImpactSnapshot
    profile: CompanyProfile
    thesis: Thesis
    promises: list[ManagementPromise] = Field(default_factory=list)
    peers: list[PeerComparison] = Field(default_factory=list)
    manual_peer_tickers: list[str] = Field(default_factory=list, max_length=6)
    certificate_urls: list[str] = Field(default_factory=list)
    deep_research: ResearchRun | None = None
    disclaimer: str
