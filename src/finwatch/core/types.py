"""Core shared types and constants. TIER 1 — do not modify."""
from __future__ import annotations

from enum import Enum

FORMULA_SUITE_VERSION = "core.v1"

DISCLAIMER = (
    "Educational analysis of public information for the portfolio owner's "
    "own decision-making. Not individualized investment advice. "
    "Data may be incomplete or delayed."
)

FORBIDDEN_VOCABULARY = [
    "guaranteed", "can't lose", "moon", "obvious", "no-brainer",
    "sure thing", "risk-free",
]

# Caution ordering: index 0 = MOST cautious. Caps may only move toward index 0.
CAUTION_ORDER = ["STRONG_REVIEW_SELL", "TRIM", "HOLD", "ACCUMULATE"]

POSTURE_MAP = {
    "STRONG_REVIEW_SELL": "critical_review",
    "TRIM": "risk_review",
    "HOLD": "monitor",
    "ACCUMULATE": "positive_support",
    "INSUFFICIENT_DATA": "insufficient_data",
}

# Red-flag codes emitted by P1 (adapter in pipeline/ maps P1 JSON -> these codes).
CRITICAL_DOC_FLAGS = frozenset({
    "item_1_03_bankruptcy",
    "item_3_01_delisting",
    "item_2_04_acceleration",
    "item_4_02_non_reliance",
    "going_concern",
    "auditor_resignation",
    "material_weakness_with_restatement_risk",
    "cyber_1_05_critical_tier",
})


class MetricStatus(str, Enum):
    COMPUTED = "computed"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class SectorClass(str, Enum):
    GENERAL = "general"
    FINANCIAL = "financial"
    INSURANCE = "insurance"
    REIT = "reit"
    UTILITY = "utility"


def sector_from_sic(sic: str | None) -> "SectorInfo":
    """v0 heuristic per spec: SIC 6000-6999 financial-class; 4900-4999 utility."""
    if not sic or not sic.strip().isdigit():
        return SectorInfo(SectorClass.GENERAL, False, sic)
    code = int(sic.strip())
    if code == 6798:
        return SectorInfo(SectorClass.REIT, True, sic)
    if 6300 <= code <= 6499:
        return SectorInfo(SectorClass.INSURANCE, True, sic)
    if 6000 <= code <= 6999:
        return SectorInfo(SectorClass.FINANCIAL, True, sic)
    if 4900 <= code <= 4999:
        return SectorInfo(SectorClass.UTILITY, False, sic)
    return SectorInfo(SectorClass.GENERAL, False, sic)


def is_manufacturer_sic(sic: str | None) -> bool:
    if not sic or not sic.strip().isdigit():
        return False
    return 2000 <= int(sic.strip()) <= 3999


class SectorInfo:
    """Lightweight sector descriptor consumed by the metrics engine."""

    __slots__ = ("sector_class", "is_financial", "sic")

    def __init__(self, sector_class: SectorClass, is_financial: bool,
                 sic: str | None = None) -> None:
        self.sector_class = sector_class
        self.is_financial = is_financial
        self.sic = sic

    def __repr__(self) -> str:  # pragma: no cover
        return f"SectorInfo({self.sector_class.value}, financial={self.is_financial})"
