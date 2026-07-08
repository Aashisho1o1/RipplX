"""Universal metric result envelope. Trust-critical (test-guarded): edit with care, keep the spec tests green.

Every metric in the system returns a MetricResult. `status` semantics:
  computed        -> value/components are meaningful
  unavailable     -> required data missing (see unavailable_missing)
  not_applicable  -> metric is conceptually wrong for this issuer
The computed/unavailable/not_applicable distinction is load-bearing: the signal
matrix skips rules (never fails globally) based on it.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from finwatch.core.types import MetricStatus


class InputUsed(BaseModel):
    concept: str
    tag: str
    taxonomy: str = "us-gaap"
    value: Optional[float] = None
    unit_ref: Optional[str] = None
    decimals: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    instant: Optional[str] = None
    accession_number: Optional[str] = None


class MetricResult(BaseModel):
    metric: str
    status: MetricStatus
    not_applicable_reason: Optional[str] = None
    unavailable_missing: list[str] = Field(default_factory=list)
    sector_applicability: list[str] = Field(default_factory=list)
    value: Optional[float] = None
    zone_or_flag: Optional[str] = None
    components: dict[str, Any] = Field(default_factory=dict)
    inputs_used: list[InputUsed] = Field(default_factory=list)
    formula_version: str
    as_of: str
    confidence: str = "high"  # high | medium | low

    @property
    def computed(self) -> bool:
        return self.status == MetricStatus.COMPUTED

    def numeric_leaves(self) -> list[float]:
        """All numeric values carried by this result (for verifier V1 matching)."""
        out: list[float] = []
        if self.value is not None:
            out.append(float(self.value))
        def walk(x: Any) -> None:
            if isinstance(x, bool):
                return
            if isinstance(x, (int, float)):
                out.append(float(x))
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    walk(v)
        walk(self.components)
        for iu in self.inputs_used:
            if iu.value is not None:
                out.append(float(iu.value))
        return out


class MetricsBundle(BaseModel):
    """Named MetricResults plus the valuation-percentile family."""
    results: dict[str, MetricResult] = Field(default_factory=dict)
    valuations: list[MetricResult] = Field(default_factory=list)  # percentile results

    def get(self, name: str) -> Optional[MetricResult]:
        return self.results.get(name)

    def computed(self, name: str) -> bool:
        r = self.get(name)
        return bool(r and r.computed)

    def all_results(self) -> list[MetricResult]:
        return list(self.results.values()) + list(self.valuations)
