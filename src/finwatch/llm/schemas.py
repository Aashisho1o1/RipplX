"""Strict Pydantic contracts for launch P1 and dormant research P2/P3.

Parsing an LLM response into these models IS the schema-validity check (V5): a
ValidationError means the stage output is malformed and must be regenerated
(stages.py routes it to StageError → the §14 regeneration policy). These schemas
P1 is deliberately small: at most three qualitative findings, each inseparable
from one to three exact filing spans. P2/P3 retain their historical research
schemas but are not constructed by the launch pipeline. All models reject unknown
fields (``extra='forbid'``); controlled enum values are normalized before use.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from finwatch.core.text_policy import contains_authored_quantity, contains_trade_instruction
from finwatch.core.types import CRITICAL_DOC_FLAGS

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


# ---- dormant P2/P3 research claim graph ------------------------------------
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
        # Historical P2 research claims keep evidence provenance. Judgment claims SHOULD
        # cite basis_claim_ids, but that is best-effort audit metadata — it is
        # never checked by V1-V5 (only persisted), and real models routinely omit it — so its
        # ABSENCE is not a hard failure here or per-stage. When basis IS provided it must
        # resolve to a declared claim (enforced in P1Output/P2Output): a P1 judgment resolves
        # within its own analysis; a P2 synthesis judgment grounds on P1's separate,
        # already-verified analysis and is therefore not resolved inline. Fact-safety (no
        # LLM-sourced numbers) is guaranteed independently by V1 + R1, not by basis_claim_ids.
        if self.claim_type == "evidence" and self.provenance is None:
            raise ValueError(f"evidence claim {self.claim_id!r} missing provenance (foundation R2)")
        return self


# ---- P1: filing event extractor (launch contract) --------------------------
class Classification(BaseModel):
    model_config = _STRICT
    overall_severity: OverallSeverity        # critical|high|medium|low|routine


class FindingEvidence(BaseModel):
    """One exact, section-relative quotation supporting a launch finding."""

    model_config = _STRICT
    accession_number: str = Field(min_length=1, max_length=64)
    form_type: str = Field(min_length=1, max_length=16)
    section_key: str = Field(min_length=1, max_length=128)
    exhibit: str | None = Field(default=None, max_length=128)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    html_element_id: str | None = Field(default=None, max_length=256)
    snippet: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _valid_exact_span(self):
        if self.char_end <= self.char_start:
            raise ValueError("evidence char_end must be greater than char_start")
        if len(self.snippet.split()) > 25:
            raise ValueError("evidence snippet must contain at most 25 words")
        return self


class Finding(BaseModel):
    """A qualitative conclusion that is inseparable from its exact SEC evidence."""

    model_config = _STRICT
    headline: str = Field(min_length=1, max_length=240)
    severity: Severity
    critical_flag: str | None = None
    evidence: list[FindingEvidence] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _qualitative_and_controlled(self):
        if contains_authored_quantity(self.headline):
            raise ValueError("finding headline must be qualitative; numbers belong in evidence")
        if contains_trade_instruction(self.headline):
            raise ValueError("finding headline must not contain trade instructions")
        if self.critical_flag is not None:
            flag = self.critical_flag.strip().lower()
            if flag not in CRITICAL_DOC_FLAGS:
                raise ValueError(
                    f"critical_flag must be one of {sorted(CRITICAL_DOC_FLAGS)}, "
                    f"got {self.critical_flag!r}"
                )
            self.critical_flag = flag
            if self.severity not in {"critical", "high"}:
                raise ValueError("critical_flag requires critical or high severity")
            if flag == "cyber_1_05_critical_tier" and self.severity != "critical":
                raise ValueError("cyber_1_05_critical_tier requires critical severity")
        return self


class P1Output(BaseModel):
    model_config = _STRICT
    accession_number: str
    ticker: str
    form_type: str
    classification: Classification
    findings: list[Finding] = Field(max_length=3)
    extraction_confidence: Confidence        # high|medium|low
    gaps: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _findings_match_classification(self):
        for finding in self.findings:
            for evidence in finding.evidence:
                if evidence.accession_number != self.accession_number:
                    raise ValueError("finding evidence accession_number must match P1 output")
                if evidence.form_type != self.form_type:
                    raise ValueError("finding evidence form_type must match P1 output")

        if not self.findings:
            if self.classification.overall_severity not in {"routine", "low"}:
                raise ValueError("medium/high/critical classification requires a finding")
            return self

        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        highest = min((finding.severity for finding in self.findings), key=rank.__getitem__)
        if self.classification.overall_severity != highest:
            raise ValueError("overall_severity must equal the highest finding severity")
        flags = [finding.critical_flag for finding in self.findings if finding.critical_flag]
        if len(flags) != len(set(flags)):
            raise ValueError("critical_flag values must be unique within a P1 output")
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
