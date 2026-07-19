"""Strict Pydantic contract for the filing-finding extractor.

Parsing an LLM response into P1Output IS the schema-validity check (V5): a
ValidationError means the output is malformed and is routed to StageError (one
bounded schema repair, then fail closed). The contract is deliberately small: at
most three qualitative findings, each inseparable from one to three exact filing
spans. All models reject unknown fields (``extra='forbid'``); controlled enum
values are normalized before use.
"""


from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from finwatch.core.types import CRITICAL_DOC_FLAGS
from finwatch.metrics.catalog import MetricId

_STRICT = ConfigDict(extra="forbid")

# Controlled vocabularies. Out-of-vocabulary values are rejected at parse time.
_SEVERITY = frozenset({"critical", "high", "medium", "low"})
_OVERALL = _SEVERITY | {"routine"}
_CONFIDENCE = frozenset({"high", "medium", "low"})
#AS: Is this vocab thing really good enough to continute? just like in another comment

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


# ---- P1: filing event extractor (launch contract) --------------------------
class Classification(BaseModel):
    model_config = _STRICT
    overall_severity: OverallSeverity        # critical|high|medium|low|routine


class FindingEvidence(BaseModel):
    """One exact, section-relative quotation supporting a launch finding.

    The model supplies only ``section_key`` and the verbatim ``snippet`` (plus the
    echoed accession/form, validated against trusted metadata). ``char_start`` and
    ``char_end`` are SERVER-DERIVED: ``llm/stages.py`` anchors the snippet in its
    canonical section (exact, unique substring) and computes the offsets; any offsets
    the model returns are ignored and overwritten. Because LLMs cannot reliably count
    characters, trusting model offsets withheld correct quotations — so the persisted
    canonical output always carries the server-computed offsets, and V4 / the canonical
    projection verify those real coordinates against the stored section text.
    """

    model_config = _STRICT
    accession_number: str = Field(min_length=1, max_length=64)
    form_type: str = Field(min_length=1, max_length=16)
    section_key: str = Field(min_length=1, max_length=128)
    exhibit: str | None = Field(default=None, max_length=128)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, gt=0)
    html_element_id: str | None = Field(default=None, max_length=256)
    snippet: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _valid_exact_span(self):
        # Offsets are server-derived: absent on model output, present (and consistent)
        # once anchored and on every persisted/re-read canonical output.
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("evidence offsets are server-derived: provide both or neither")
        if self.char_start is not None and self.char_end <= self.char_start:
            raise ValueError("evidence char_end must be greater than char_start")
        if len(self.snippet.split()) > 25:
            raise ValueError("evidence snippet must contain at most 25 words")
        return self


class Finding(BaseModel):
    """A qualitative conclusion that is inseparable from its exact SEC evidence."""

    model_config = _STRICT
    finding_id: Literal["f1", "f2", "f3"]
    headline: str = Field(min_length=1, max_length=240)
    severity: Severity
    critical_flag: str | None = None
    metric_id: MetricId | None = None
    direction: Literal["up", "down", "flat"] | None = None
    evidence: list[FindingEvidence] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _qualitative_and_controlled(self):
        if (self.metric_id is None) != (self.direction is None):
            raise ValueError("metric_id and direction must appear together")
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
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding_id values must be unique within a P1 output")
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
