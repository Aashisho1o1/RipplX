"""Pydantic stage schemas mirroring the JSON contracts in CLAUDE.md §§11–13.

Parsing an LLM response into these models IS the schema-validity check (V5): a
ValidationError means the stage output is malformed and must be regenerated
(stages.py routes it to StageError → the §14 regeneration policy). These schemas
enforce STRUCTURE, required fields, the controlled vocabularies the matrix/digest
consume, the claim graph (foundation R2: evidence ⇒ provenance, judgment ⇒ basis),
and reject unknown fields (``extra='forbid'``). Enum values are normalised (strip +
lowercase) so a well-formed but differently-cased value is accepted, not silently
passed through as garbage.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, field_validator, model_validator

_STRICT = ConfigDict(extra="forbid")

# Controlled vocabularies (CLAUDE.md §§11–13). Out-of-vocabulary values are rejected
# at parse time rather than reaching the matrix's exact-string gates as unknowns.
_SEVERITY = frozenset({"critical", "high", "medium", "low"})
_OVERALL = _SEVERITY | {"routine"}
_CONFIDENCE = frozenset({"high", "medium", "low"})
_GUIDANCE = frozenset({"raised", "maintained", "lowered", "withdrawn", "initiated", "none_stated"})
_LIQUIDITY = frozenset({"strengthening", "stable", "deteriorating", "unclear"})
_DIRECTION = frozenset({"positive", "negative", "neutral", "unclear"})
_IMPACT = frozenset({"direct", "indirect", "no_impact"})
_THESIS = frozenset({"intact", "weakened", "broken", "not_assessable"})
_POSTURE = frozenset({"critical_review", "risk_review", "monitor", "positive_support",
                      "insufficient_data"})
_CLAIM_TYPE = frozenset({"evidence", "judgment"})


def _one_of(allowed: frozenset[str]):
    def _validate(value: str) -> str:
        v = value.strip().lower() if isinstance(value, str) else value
        if v not in allowed:
            raise ValueError(f"must be one of {sorted(allowed)}, got {value!r}")
        return v
    return _validate


Severity = Annotated[str, AfterValidator(_one_of(_SEVERITY))]
OverallSeverity = Annotated[str, AfterValidator(_one_of(_OVERALL))]
Confidence = Annotated[str, AfterValidator(_one_of(_CONFIDENCE))]
Guidance = Annotated[str, AfterValidator(_one_of(_GUIDANCE))]
Liquidity = Annotated[str, AfterValidator(_one_of(_LIQUIDITY))]
Direction = Annotated[str, AfterValidator(_one_of(_DIRECTION))]
ImpactClass = Annotated[str, AfterValidator(_one_of(_IMPACT))]
ThesisVerdict = Annotated[str, AfterValidator(_one_of(_THESIS))]
Posture = Annotated[str, AfterValidator(_one_of(_POSTURE))]
ClaimType = Annotated[str, AfterValidator(_one_of(_CLAIM_TYPE))]


def _require_unique_claim_ids(claims: list) -> list:
    ids = [c.claim_id for c in claims]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate claim_id in claims (must be unique within a stage output)")
    return claims


# ---- claim graph (shared, foundation R2) -----------------------------------
class Provenance(BaseModel):
    model_config = _STRICT
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
    model_config = _STRICT
    claim_id: str
    claim_type: ClaimType                    # 'evidence' | 'judgment'
    text: str
    confidence: Confidence | None = None
    provenance: Provenance | None = None  # required for evidence claims
    basis_claim_ids: list[str] | None = None  # required for judgment claims

    @model_validator(mode="after")
    def _claim_graph(self):
        # Foundation R2: evidence claims are verbatim-anchored (need provenance);
        # judgment claims interpret and MUST cite basis_claim_ids.
        if self.claim_type == "evidence" and self.provenance is None:
            raise ValueError(f"evidence claim {self.claim_id!r} missing provenance (foundation R2)")
        if self.claim_type == "judgment" and not self.basis_claim_ids:
            raise ValueError(f"judgment claim {self.claim_id!r} missing basis_claim_ids")
        return self


# ---- P1: filing event extractor (§11) --------------------------------------
class Item8K(BaseModel):
    model_config = _STRICT
    item: str
    base_severity: str
    final_severity: Severity                 # critical|high|medium|low
    adjustment_rationale_claim_id: str | None = None


class Classification(BaseModel):
    model_config = _STRICT
    items_8k: list[Item8K] = []
    overall_severity: OverallSeverity        # critical|high|medium|low|routine


class MaterialItem(BaseModel):
    model_config = _STRICT
    headline: str
    event_type: str
    severity: Severity
    claim_ids: list[str] = []


class RiskFactorFindings(BaseModel):
    model_config = _STRICT
    added: list[str] = []
    removed: list[str] = []
    modified: list[str] = []


class GuidanceDirection(BaseModel):
    model_config = _STRICT
    # value: raised|maintained|lowered|withdrawn|initiated|none_stated
    value: Guidance
    claim_id: str | None = None


class RedFlag(BaseModel):
    model_config = _STRICT
    flag: str
    severity: Severity
    claim_ids: list[str] = []


class P1Output(BaseModel):
    model_config = _STRICT
    accession_number: str
    ticker: str
    form_type: str
    classification: Classification
    claims: list[Claim] = []
    material_items: list[MaterialItem] = []
    risk_factor_findings: RiskFactorFindings | None = None
    guidance_direction: GuidanceDirection
    red_flags: list[RedFlag] = []
    extraction_confidence: Confidence        # high|medium|low
    gaps: list[str] = []

    @field_validator("claims")
    @classmethod
    def _unique_claims(cls, v):
        return _require_unique_claim_ids(v)

    @model_validator(mode="after")
    def _claim_refs_resolve(self):
        # Every claim_id cited by a red flag or material item must be a declared claim.
        ids = {c.claim_id for c in self.claims}
        for rf in self.red_flags:
            for cid in rf.claim_ids:
                if cid not in ids:
                    raise ValueError(f"red_flag {rf.flag!r} cites unknown claim_id {cid!r}")
        for mi in self.material_items:
            for cid in mi.claim_ids:
                if cid not in ids:
                    raise ValueError(f"material_item cites unknown claim_id {cid!r}")
        return self


# ---- P2: portfolio impact explainer (§12) ----------------------------------
class ThesisCheck(BaseModel):
    model_config = _STRICT
    verdict: ThesisVerdict                   # intact|weakened|broken|not_assessable
    judgment_claim_id: str | None = None


class NetRead(BaseModel):
    model_config = _STRICT
    text: str
    judgment_claim_id: str | None = None


class RecordAffected(BaseModel):
    model_config = _STRICT
    ticker: str
    owned: bool
    impact_class: ImpactClass                # direct|indirect|no_impact
    channels: dict[str, Any] = {}
    guidance_direction: Guidance
    liquidity_read: Liquidity                # strengthening|stable|deteriorating|unclear
    net_direction: Direction                 # positive|negative|neutral|unclear
    thesis_check: ThesisCheck
    net_read: NetRead
    confidence: Confidence


class P2Output(BaseModel):
    model_config = _STRICT
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
    model_config = _STRICT
    rule: str
    reason: str


class EscalationRequest(BaseModel):
    model_config = _STRICT
    to: str
    justification: str


class P3Output(BaseModel):
    model_config = _STRICT
    ticker: str
    accession_number: str
    review_posture: Posture
    trade_action: str | None = None       # must be null in default mode (enforced by V5)
    hypothetical_signal: str
    rules_fired: list[str] = []
    rules_skipped: list[RuleSkipped] = []
    computed_inputs: list[Any] = []
    rationale: str
    counter_evidence: str
    what_would_change_this: list[str] = []
    escalation_request: EscalationRequest | None = None
    confidence: Confidence
    disclaimer: str
