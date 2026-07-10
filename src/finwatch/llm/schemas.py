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
        # Foundation R2: evidence claims are verbatim-anchored (need provenance).
        # Judgment claims must cite basis, but WHERE that basis lives is stage-specific,
        # so presence/resolution is enforced per stage: a P1 judgment grounds on evidence
        # in the SAME analysis (enforced in P1Output._claim_refs_resolve); a P2 synthesis
        # judgment grounds on P1's evidence — a separate, already-verified analysis — so it
        # is not required inline here (see P2Output._claim_refs_resolve).
        if self.claim_type == "evidence" and self.provenance is None:
            raise ValueError(f"evidence claim {self.claim_id!r} missing provenance (foundation R2)")
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
        # Every claim_id cited ANYWHERE must resolve to a declared claim — not just
        # red-flag/material-item refs but also judgment basis_claim_ids, the
        # guidance_direction claim, and each 8-K adjustment rationale. Otherwise an
        # (LLM- or injection-authored) judgment could rest on a fabricated evidence id.
        ids = {c.claim_id for c in self.claims}

        def _check(cid, where):
            if cid is not None and cid not in ids:
                raise ValueError(f"{where} cites unknown claim_id {cid!r}")

        for rf in self.red_flags:
            for cid in rf.claim_ids:
                _check(cid, f"red_flag {rf.flag!r}")
        for mi in self.material_items:
            for cid in mi.claim_ids:
                _check(cid, "material_item")
        for c in self.claims:
            # P1 is self-contained: a judgment must cite basis, and it must resolve here.
            if c.claim_type == "judgment" and not c.basis_claim_ids:
                raise ValueError(
                    f"judgment claim {c.claim_id!r} missing basis_claim_ids (foundation R2)")
            for cid in (c.basis_claim_ids or ()):
                _check(cid, f"judgment claim {c.claim_id!r} basis")
        _check(self.guidance_direction.claim_id, "guidance_direction")
        for it in self.classification.items_8k:
            _check(it.adjustment_rationale_claim_id, f"item_8k {it.item!r}")
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

    @model_validator(mode="after")
    def _claim_refs_resolve(self):
        # A P2 synthesis judgment (thesis_check / net_read) grounds on P1's evidence,
        # which lives in the SEPARATE P1 analysis — so its basis_claim_ids reference ids
        # that are neither declared nor resolvable here, and are not required inline (P1's
        # evidence is verified in P1). We DO require each record's thesis_check / net_read
        # judgment_claim_id to resolve to a judgment claim declared in THIS output.
        ids = {c.claim_id for c in self.claims}

        def _check(cid, where):
            if cid is not None and cid not in ids:
                raise ValueError(f"{where} cites unknown claim_id {cid!r}")

        for rec in self.records_affected:
            _check(rec.thesis_check.judgment_claim_id, f"{rec.ticker} thesis_check")
            _check(rec.net_read.judgment_claim_id, f"{rec.ticker} net_read")
        return self


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
