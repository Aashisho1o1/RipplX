"""Pydantic stage schemas mirroring the JSON contracts in CLAUDE.md §§11–13.

Parsing an LLM response into these models IS the schema-validity check: a
ValidationError means the stage output is malformed and must be regenerated. Enum
*values* (guidance_direction, net_direction, …) are carried as ``str`` here and
normalised/enforced by the pipeline adapters (they map to the matrix's exact
vocabulary); the schema enforces STRUCTURE, required fields, and types.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator


def _require_unique_claim_ids(claims: list) -> list:
    ids = [c.claim_id for c in claims]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate claim_id in claims (must be unique within a stage output)")
    return claims


# ---- claim graph (shared, foundation R2) -----------------------------------
class Provenance(BaseModel):
    accession_number: str
    form_type: str
    section_key: str
    exhibit: str | None = None
    char_start: int
    char_end: int
    html_element_id: str | None = None
    text_sha256_prefix: str
    snippet: str
    xbrl: dict[str, Any] | None = None


class Claim(BaseModel):
    claim_id: str
    claim_type: str                          # 'evidence' | 'judgment'
    text: str
    confidence: str | None = None
    provenance: Provenance | None = None  # required for evidence claims
    basis_claim_ids: list[str] | None = None  # required for judgment claims


# ---- P1: filing event extractor (§11) --------------------------------------
class Item8K(BaseModel):
    item: str
    base_severity: str
    final_severity: str                      # critical|high|medium|low
    adjustment_rationale_claim_id: str | None = None


class Classification(BaseModel):
    items_8k: list[Item8K] = []
    overall_severity: str                    # critical|high|medium|low|routine


class MaterialItem(BaseModel):
    headline: str
    event_type: str
    severity: str
    claim_ids: list[str] = []


class RiskFactorFindings(BaseModel):
    added: list[str] = []
    removed: list[str] = []
    modified: list[str] = []


class GuidanceDirection(BaseModel):
    # value: raised|maintained|lowered|withdrawn|initiated|none_stated
    value: str
    claim_id: str | None = None


class RedFlag(BaseModel):
    flag: str
    severity: str
    claim_ids: list[str] = []


class P1Output(BaseModel):
    accession_number: str
    ticker: str
    form_type: str
    classification: Classification
    claims: list[Claim] = []
    material_items: list[MaterialItem] = []
    risk_factor_findings: RiskFactorFindings | None = None
    guidance_direction: GuidanceDirection
    red_flags: list[RedFlag] = []
    extraction_confidence: str               # high|medium|low
    gaps: list[str] = []

    @field_validator("claims")
    @classmethod
    def _unique_claims(cls, v):
        return _require_unique_claim_ids(v)


# ---- P2: portfolio impact explainer (§12) ----------------------------------
class ThesisCheck(BaseModel):
    verdict: str                             # intact|weakened|broken|not_assessable
    judgment_claim_id: str | None = None


class NetRead(BaseModel):
    text: str
    judgment_claim_id: str | None = None


class RecordAffected(BaseModel):
    ticker: str
    owned: bool
    impact_class: str                        # direct|indirect|no_impact
    channels: dict[str, Any] = {}
    guidance_direction: str
    liquidity_read: str                      # strengthening|stable|deteriorating|unclear
    net_direction: str                       # positive|negative|neutral|unclear
    thesis_check: ThesisCheck
    net_read: NetRead
    confidence: str


class P2Output(BaseModel):
    accession_number: str
    records_affected: list[RecordAffected] = []
    claims: list[Claim] = []
    portfolio_level_notes: str | None = None

    @field_validator("claims")
    @classmethod
    def _unique_claims(cls, v):
        return _require_unique_claim_ids(v)


# ---- P3: signal rationale (§13.3; consumed in Phase 6) ---------------------
class RuleSkipped(BaseModel):
    rule: str
    reason: str


class EscalationRequest(BaseModel):
    to: str
    justification: str


class P3Output(BaseModel):
    ticker: str
    accession_number: str
    review_posture: str
    trade_action: str | None = None       # must be null in default mode
    hypothetical_signal: str
    rules_fired: list[str] = []
    rules_skipped: list[RuleSkipped] = []
    computed_inputs: list[Any] = []
    rationale: str
    counter_evidence: str
    what_would_change_this: list[str] = []
    escalation_request: EscalationRequest | None = None
    confidence: str
    disclaimer: str
