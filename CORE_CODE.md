# finwatch — CORE_CODE.md
## Pre-written critical code (TIER 1) — transcribe VERBATIM

**Instructions to the building agent (Claude Code):**

1. These files were written by the strongest available model and reviewed as the project's
   trust layer. **Transcribe each file byte-for-byte to the exact path shown.** Do not
   refactor, rename, reformat, reorder imports, "modernize," or add features.
2. Build all Tier 2/3 code (see SYSTEM_DESIGN.md) to fit these interfaces. If an interface
   genuinely cannot work, **STOP and report to the operator** — never adapt Tier 1 code.
3. Immediately after transcription (end of Phase 0), run
   `uv run pytest tests/test_signals_matrix.py tests/test_verifier_mutations.py -q`.
   These tests are self-contained (no network, no DB). **They must pass before Phase 1
   begins.** If they fail, the transcription is wrong — diff against this document.
4. Only dependency required by Tier 1 code: `pydantic>=2`. Everything else is stdlib.

Files in this document:
- `src/finwatch/core/types.py`
- `src/finwatch/metrics/envelope.py`
- `src/finwatch/xbrl/normalize.py`
- `src/finwatch/metrics/formulas.py`
- `src/finwatch/signals/matrix.py`
- `src/finwatch/verify/checks.py`
- `tests/test_signals_matrix.py`
- `tests/test_verifier_mutations.py`

---

## FILE: `src/finwatch/core/types.py`

```python
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
```

---

## FILE: `src/finwatch/metrics/envelope.py`

```python
"""Universal metric result envelope. TIER 1 — do not modify.

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
```

---

## FILE: `src/finwatch/xbrl/normalize.py`

```python
"""XBRL normalization layer over SEC companyfacts JSON. TIER 1 — do not modify.

Design (per CLAUDE.md §8):
  * Concept resolution via priority-ordered tag lists; first tag with usable
    facts wins; the tag actually used is recorded on every resolved fact.
  * Amendment supersession: for an identical (tag, unit, period), the fact from
    the latest `filed` date (tie-break: accession number) wins. Superseded
    values are retained in memory but excluded from series.
  * Duration classification: ANNUAL 300-400 days; QUARTER 60-120 days. YTD and
    other durations are ignored by annual()/quarterly() on purpose.
  * companyfacts contains undimensioned (consolidated) facts only, which
    satisfies the consolidated-only rule at this layer.
Pure stdlib + pydantic. No I/O.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from pydantic import BaseModel

from finwatch.metrics.envelope import InputUsed

# Priority-ordered tag lists. "dei:" prefix selects the dei taxonomy.
CONCEPT_MAP: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
        "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "cash_change": [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
        "PeriodIncreaseDecreaseIncludingExchangeRateEffect",
        "CashAndCashEquivalentsPeriodIncreaseDecrease",
    ],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "st_debt": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt"],
    "gross_profit": ["GrossProfit"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "operating_income": ["OperatingIncomeLoss"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "shares_outstanding": [
        "dei:EntityCommonStockSharesOutstanding",
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "inventory": ["InventoryNet"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "ppe_net": ["PropertyPlantAndEquipmentNet"],
    "dep_amort": [
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        "Depreciation",
    ],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "fx_effect_on_cash": [
        "EffectOfExchangeRateOnCashAndCashEquivalents",
        "EffectOfExchangeRateOnCashCashEquivalents"
        "RestrictedCashAndRestrictedCashEquivalents",
    ],
}

_ANNUAL = (300, 400)
_QUARTER = (60, 120)


class Fact(BaseModel):
    taxonomy: str
    tag: str
    unit: str
    value: float
    start: Optional[str] = None   # ISO date for durations
    end: Optional[str] = None     # ISO date: duration end, or instant date
    fy: Optional[int] = None
    fp: Optional[str] = None
    form: Optional[str] = None
    accn: Optional[str] = None
    filed: Optional[str] = None
    frame: Optional[str] = None

    @property
    def is_instant(self) -> bool:
        return self.start is None

    @property
    def duration_days(self) -> Optional[int]:
        if self.start is None or self.end is None:
            return None
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days

    def period_key(self) -> tuple:
        return (self.tag, self.unit, self.start or "", self.end or "")


class ResolvedFact(BaseModel):
    """A Fact annotated with the concept it satisfied."""
    concept: str
    fact: Fact

    def to_input_used(self) -> InputUsed:
        f = self.fact
        return InputUsed(
            concept=self.concept, tag=f.tag, taxonomy=f.taxonomy,
            value=f.value, unit_ref=f.unit, decimals=None,
            period_start=f.start,
            period_end=None if f.is_instant else f.end,
            instant=f.end if f.is_instant else None,
            accession_number=f.accn,
        )


class FactStore:
    def __init__(self, facts: Iterable[Fact]) -> None:
        deduped: dict[tuple, Fact] = {}
        for f in facts:
            k = f.period_key()
            cur = deduped.get(k)
            if cur is None or _supersedes(f, cur):
                deduped[k] = f
        self._facts = list(deduped.values())
        self._by_tag: dict[tuple[str, str], list[Fact]] = {}
        for f in self._facts:
            self._by_tag.setdefault((f.taxonomy, f.tag), []).append(f)

    # -- construction -----------------------------------------------------
    @classmethod
    def from_companyfacts(cls, cf_json: dict) -> "FactStore":
        out: list[Fact] = []
        for taxonomy, tags in (cf_json.get("facts") or {}).items():
            for tag, body in tags.items():
                for unit, entries in (body.get("units") or {}).items():
                    for e in entries:
                        if e.get("val") is None:
                            continue
                        out.append(Fact(
                            taxonomy=taxonomy, tag=tag, unit=unit,
                            value=float(e["val"]),
                            start=e.get("start"), end=e.get("end"),
                            fy=e.get("fy"), fp=e.get("fp"), form=e.get("form"),
                            accn=e.get("accn"), filed=e.get("filed"),
                            frame=e.get("frame"),
                        ))
        return cls(out)

    # -- concept resolution ------------------------------------------------
    def _tags_for(self, concept: str) -> list[tuple[str, str]]:
        tags = CONCEPT_MAP.get(concept)
        if tags is None:
            raise KeyError(f"unknown concept: {concept}")
        out = []
        for t in tags:
            if t.startswith("dei:"):
                out.append(("dei", t.split(":", 1)[1]))
            else:
                out.append(("us-gaap", t))
        return out

    def _facts_for(self, concept: str) -> list[ResolvedFact]:
        for taxo, tag in self._tags_for(concept):
            fs = self._by_tag.get((taxo, tag))
            if fs:
                return [ResolvedFact(concept=concept, fact=f) for f in fs]
        return []

    # -- series accessors ---------------------------------------------------
    def annual(self, concept: str, n: int = 6) -> list[ResolvedFact]:
        """Annual-duration facts, newest first, one per period end."""
        return self._duration_series(concept, _ANNUAL, n)

    def quarterly(self, concept: str, n: int = 10) -> list[ResolvedFact]:
        return self._duration_series(concept, _QUARTER, n)

    def instant(self, concept: str, n: int = 6) -> list[ResolvedFact]:
        """Instant facts, newest first, one per date."""
        rows = [r for r in self._facts_for(concept) if r.fact.is_instant]
        rows.sort(key=lambda r: r.fact.end or "", reverse=True)
        seen, out = set(), []
        for r in rows:
            if r.fact.end in seen:
                continue
            seen.add(r.fact.end)
            out.append(r)
            if len(out) >= n:
                break
        return out

    def _duration_series(self, concept: str, window: tuple[int, int],
                         n: int) -> list[ResolvedFact]:
        lo, hi = window
        rows = [r for r in self._facts_for(concept)
                if not r.fact.is_instant
                and r.fact.duration_days is not None
                and lo <= r.fact.duration_days <= hi]
        rows.sort(key=lambda r: r.fact.end or "", reverse=True)
        seen, out = set(), []
        for r in rows:
            if r.fact.end in seen:
                continue
            seen.add(r.fact.end)
            out.append(r)
            if len(out) >= n:
                break
        return out

    # -- convenience ---------------------------------------------------------
    def latest_annual(self, concept: str) -> Optional[ResolvedFact]:
        s = self.annual(concept, 1)
        return s[0] if s else None

    def latest_instant(self, concept: str) -> Optional[ResolvedFact]:
        s = self.instant(concept, 1)
        return s[0] if s else None

    def yoy_pair(self, concept: str, kind: str = "annual"
                 ) -> Optional[tuple[ResolvedFact, ResolvedFact]]:
        """(current, prior) annual pair or (current, same-quarter-last-year)."""
        if kind == "annual":
            s = self.annual(concept, 2)
            return (s[0], s[1]) if len(s) >= 2 else None
        s = self.quarterly(concept, 8)
        if not s:
            return None
        cur = s[0]
        cur_end = date.fromisoformat(cur.fact.end)
        for cand in s[1:]:
            d = (cur_end - date.fromisoformat(cand.fact.end)).days
            if 330 <= d <= 400:
                return (cur, cand)
        return None

    def instant_pair(self, concept: str
                     ) -> Optional[tuple[ResolvedFact, ResolvedFact]]:
        """(current, ~one-year-prior) instant pair for balance-sheet deltas."""
        s = self.instant(concept, 8)
        if not s:
            return None
        cur = s[0]
        cur_end = date.fromisoformat(cur.fact.end)
        for cand in s[1:]:
            d = (cur_end - date.fromisoformat(cand.fact.end)).days
            if 330 <= d <= 400:
                return (cur, cand)
        return None


def _supersedes(new: Fact, old: Fact) -> bool:
    """Latest `filed` wins; tie-break on accession number. Deterministic."""
    nf, of_ = new.filed or "", old.filed or ""
    if nf != of_:
        return nf > of_
    return (new.accn or "") > (old.accn or "")
```

---

## FILE: `src/finwatch/metrics/formulas.py`

```python
"""Sector-aware metric formulas. TIER 1 — do not modify.

Every function returns a MetricResult (envelope.py). Rules:
  * Never raise on missing data — return status=unavailable with the missing list.
  * Sector inapplicability -> status=not_applicable with a reason.
  * All inputs recorded in inputs_used; all formulas versioned.
`compute_all` is the single entry point the pipeline may call.
"""
from __future__ import annotations

import math
from typing import Optional, Protocol, Sequence

from pydantic import BaseModel

from finwatch.core.types import MetricStatus, SectorInfo, is_manufacturer_sic
from finwatch.metrics.envelope import InputUsed, MetricResult, MetricsBundle
from finwatch.xbrl.normalize import FactStore, ResolvedFact


class PriceProvider(Protocol):
    def close_on_or_before(self, ticker: str, date_iso: str) -> Optional[float]: ...


class Holding(BaseModel):
    ticker: str
    owned: bool = True
    shares: Optional[float] = None
    cost_basis: Optional[float] = None
    current_weight_pct: Optional[float] = None
    target_weight_pct: Optional[float] = None
    thesis: Optional[str] = None
    horizon: Optional[str] = None


# ---------------------------------------------------------------- helpers --
def _res(metric: str, version: str, as_of: str, **kw) -> MetricResult:
    return MetricResult(metric=metric, formula_version=version, as_of=as_of, **kw)


def _unavailable(metric, version, as_of, missing, inputs=()):
    return _res(metric, version, as_of, status=MetricStatus.UNAVAILABLE,
                unavailable_missing=list(missing),
                inputs_used=[r.to_input_used() for r in inputs])


def _na(metric, version, as_of, reason, applicability):
    return _res(metric, version, as_of, status=MetricStatus.NOT_APPLICABLE,
                not_applicable_reason=reason, sector_applicability=applicability)


def _val(rf: Optional[ResolvedFact]) -> Optional[float]:
    return None if rf is None else rf.fact.value


def _collect(*rfs: Optional[ResolvedFact]) -> list[InputUsed]:
    return [r.to_input_used() for r in rfs if r is not None]


def _need(pairs: dict[str, Optional[ResolvedFact]]):
    """Return (missing_names, present_facts)."""
    missing = [k for k, v in pairs.items() if v is None]
    present = [v for v in pairs.values() if v is not None]
    return missing, present


def _direction(values_newest_first: Sequence[float]) -> str:
    v = list(values_newest_first)
    if len(v) < 3:
        return "insufficient_points"
    chron = list(reversed(v))
    ups = all(b > a for a, b in zip(chron, chron[1:]))
    downs = all(b < a for a, b in zip(chron, chron[1:]))
    return "up" if ups else "down" if downs else "mixed"


# ---------------------------------------------------------------- metrics --
def revenue_growth(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "revenue_growth.v1"
    pair = store.yoy_pair("revenue")
    if pair is None:
        return _unavailable("revenue_growth", V, as_of, ["revenue (2 annual periods)"])
    cur, prior = pair
    if prior.fact.value == 0:
        return _unavailable("revenue_growth", V, as_of, ["prior revenue is zero"],
                            inputs=(cur, prior))
    yoy = (cur.fact.value - prior.fact.value) / abs(prior.fact.value)
    q = store.quarterly("revenue", 4)
    ttm = sum(r.fact.value for r in q) if len(q) == 4 else None
    comps = {"yoy": round(yoy, 6)}
    if ttm is not None:
        comps["ttm_revenue"] = ttm
    return _res("revenue_growth", V, as_of, status=MetricStatus.COMPUTED,
                value=round(yoy, 6), components=comps,
                inputs_used=_collect(cur, prior, *q),
                sector_applicability=["universal"])


def _trend_metric(name: str, concept: str, store: FactStore, as_of: str) -> MetricResult:
    V = f"{name}.v1"
    pair = store.yoy_pair(concept)
    if pair is None:
        return _unavailable(name, V, as_of, [f"{concept} (2 annual periods)"])
    cur, prior = pair
    denom = abs(prior.fact.value)
    yoy = None if denom == 0 else (cur.fact.value - prior.fact.value) / denom
    qs = store.quarterly(concept, 4)
    comps = {"current": cur.fact.value, "prior": prior.fact.value,
             "four_quarter_direction": _direction([r.fact.value for r in qs])}
    if yoy is not None:
        comps["yoy"] = round(yoy, 6)
    return _res(name, V, as_of, status=MetricStatus.COMPUTED,
                value=None if yoy is None else round(yoy, 6), components=comps,
                inputs_used=_collect(cur, prior, *qs),
                sector_applicability=["universal"])


def net_income_trend(store, sector, as_of):  # noqa: D103
    return _trend_metric("net_income_trend", "net_income", store, as_of)


def cfo_trend(store, sector, as_of):  # noqa: D103
    return _trend_metric("cfo_trend", "cfo", store, as_of)


def liquidity_basics(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "liquidity_basics.v1"
    cash = store.latest_instant("cash")
    lt = store.latest_instant("lt_debt")
    st = store.latest_instant("st_debt")
    if cash is None:
        return _unavailable("liquidity_basics", V, as_of, ["cash"])
    total_debt = (_val(lt) or 0.0) + (_val(st) or 0.0)
    comps = {"cash": cash.fact.value, "total_debt": total_debt,
             "net_debt": total_debt - cash.fact.value}
    inputs = _collect(cash, lt, st)
    if not sector.is_financial:
        ca, cl = store.latest_instant("current_assets"), store.latest_instant("current_liabilities")
        if ca is not None and cl is not None and cl.fact.value != 0:
            comps["current_ratio"] = round(ca.fact.value / cl.fact.value, 4)
            inputs += _collect(ca, cl)
        else:
            comps["current_ratio"] = None
    else:
        comps["current_ratio_note"] = "not_applicable_financial_institution"
    return _res("liquidity_basics", V, as_of, status=MetricStatus.COMPUTED,
                components=comps, inputs_used=inputs,
                sector_applicability=["universal (current_ratio excluded for financials)"])


def share_count_change(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "share_count_change.v1"
    pair = store.instant_pair("shares_outstanding")
    if pair is None:
        ann = store.yoy_pair("shares_outstanding")
        if ann is None:
            return _unavailable("share_count_change", V, as_of,
                                ["shares_outstanding (2 comparable points)"])
        pair = ann
    cur, prior = pair
    if prior.fact.value == 0:
        return _unavailable("share_count_change", V, as_of, ["prior share count is zero"])
    chg = (cur.fact.value - prior.fact.value) / prior.fact.value
    return _res("share_count_change", V, as_of, status=MetricStatus.COMPUTED,
                value=round(chg, 6),
                components={"current": cur.fact.value, "prior": prior.fact.value},
                inputs_used=_collect(cur, prior), sector_applicability=["universal"])


def simple_leverage(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "simple_leverage.v1"
    APPL = ["general", "manufacturer", "utility"]
    if sector.is_financial:
        return _na("simple_leverage", V, as_of, "financial_institution", APPL)
    op = store.latest_annual("operating_income")
    da = store.latest_annual("dep_amort")
    cash = store.latest_instant("cash")
    lt, st = store.latest_instant("lt_debt"), store.latest_instant("st_debt")
    ie = store.latest_annual("interest_expense")
    missing, _ = _need({"operating_income": op, "cash": cash})
    if missing:
        return _unavailable("simple_leverage", V, as_of, missing)
    ebitda_proxy = op.fact.value + (_val(da) or 0.0)
    net_debt = (_val(lt) or 0.0) + (_val(st) or 0.0) - cash.fact.value
    comps: dict = {"ebitda_proxy": ebitda_proxy, "net_debt": net_debt}
    if ebitda_proxy > 0:
        comps["net_debt_to_ebitda"] = round(net_debt / ebitda_proxy, 4)
    if ie is not None and ie.fact.value not in (0, None):
        comps["interest_coverage"] = round(op.fact.value / ie.fact.value, 4)
    return _res("simple_leverage", V, as_of, status=MetricStatus.COMPUTED,
                value=comps.get("net_debt_to_ebitda"), components=comps,
                inputs_used=_collect(op, da, cash, lt, st, ie),
                sector_applicability=APPL,
                confidence="medium")  # EBITDA proxy, not reported EBITDA


def piotroski_f(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    """9 binary signals; financials skip current-ratio and gross-margin
    components per spec, and the score is scaled to a 9-point equivalent
    (components['score_scaled_9']) for matrix thresholds."""
    V = "piotroski_f.v1"
    need = {
        "net_income": store.yoy_pair("net_income"),
        "total_assets": store.instant_pair("total_assets"),
        "cfo": store.yoy_pair("cfo"),
        "revenue": store.yoy_pair("revenue"),
        "lt_debt": store.instant_pair("lt_debt"),
        "shares": store.instant_pair("shares_outstanding"),
    }
    missing = [k for k, v in need.items() if v is None and k in
               ("net_income", "total_assets", "cfo", "revenue")]
    if missing:
        return _unavailable("piotroski_f", V, as_of,
                            [f"{m} (current+prior)" for m in missing])
    (ni_c, ni_p) = need["net_income"]; (ta_c, ta_p) = need["total_assets"]
    (cf_c, cf_p) = need["cfo"]; (rv_c, rv_p) = need["revenue"]
    inputs = _collect(ni_c, ni_p, ta_c, ta_p, cf_c, cf_p, rv_c, rv_p)

    roa_c = ni_c.fact.value / ta_c.fact.value if ta_c.fact.value else None
    roa_p = ni_p.fact.value / ta_p.fact.value if ta_p.fact.value else None
    comps: dict = {}
    evaluated = 0
    score = 0

    def sig(name: str, cond: Optional[bool]) -> None:
        nonlocal evaluated, score
        if cond is None:
            comps[name] = "skipped"
            return
        evaluated += 1
        comps[name] = bool(cond)
        score += int(cond)

    sig("f1_roa_positive", None if roa_c is None else roa_c > 0)
    sig("f2_cfo_positive", cf_c.fact.value > 0)
    sig("f3_delta_roa_positive",
        None if roa_c is None or roa_p is None else roa_c > roa_p)
    sig("f4_accruals_cfo_gt_ni", cf_c.fact.value > ni_c.fact.value)

    if need["lt_debt"] is not None:
        (ld_c, ld_p) = need["lt_debt"]
        inputs += _collect(ld_c, ld_p)
        lev_c = ld_c.fact.value / ta_c.fact.value if ta_c.fact.value else None
        lev_p = ld_p.fact.value / ta_p.fact.value if ta_p.fact.value else None
        sig("f5_leverage_decreased",
            None if lev_c is None or lev_p is None else lev_c < lev_p)
    else:
        sig("f5_leverage_decreased", True)  # no LT debt reported ≈ no leverage increase
        comps["f5_note"] = "no_lt_debt_reported_treated_as_pass"

    if sector.is_financial:
        comps["f6_current_ratio_improved"] = "skipped_financial"
        comps["f8_gross_margin_improved"] = "skipped_financial"
    else:
        ca, cl = store.instant_pair("current_assets"), store.instant_pair("current_liabilities")
        if ca and cl and cl[0].fact.value and cl[1].fact.value:
            inputs += _collect(*ca, *cl)
            sig("f6_current_ratio_improved",
                (ca[0].fact.value / cl[0].fact.value)
                > (ca[1].fact.value / cl[1].fact.value))
        else:
            sig("f6_current_ratio_improved", None)
        gp = store.yoy_pair("gross_profit")
        if gp and rv_c.fact.value and rv_p.fact.value:
            inputs += _collect(*gp)
            sig("f8_gross_margin_improved",
                (gp[0].fact.value / rv_c.fact.value)
                > (gp[1].fact.value / rv_p.fact.value))
        else:
            sig("f8_gross_margin_improved", None)

    if need["shares"] is not None:
        (sh_c, sh_p) = need["shares"]
        inputs += _collect(sh_c, sh_p)
        sig("f7_no_new_shares", sh_c.fact.value <= sh_p.fact.value * 1.01)
    else:
        sig("f7_no_new_shares", None)

    if all(x.fact.value for x in (ta_c, ta_p)):
        sig("f9_asset_turnover_improved",
            (rv_c.fact.value / ta_c.fact.value)
            > (rv_p.fact.value / ta_p.fact.value))
    else:
        sig("f9_asset_turnover_improved", None)

    if evaluated == 0:
        return _unavailable("piotroski_f", V, as_of, ["no components evaluable"])
    comps["components_evaluated"] = evaluated
    comps["score_scaled_9"] = round(score * 9 / evaluated)
    return _res("piotroski_f", V, as_of, status=MetricStatus.COMPUTED,
                value=float(score), components=comps, inputs_used=inputs,
                sector_applicability=["universal (reduced set for financials)"],
                confidence="high" if evaluated >= 8 else "medium")


def altman_z(store: FactStore, sector: SectorInfo, as_of: str, *,
             ticker: str, price_provider: Optional[PriceProvider]) -> MetricResult:
    """Original Z for manufacturers with a price; Z'' (book equity) otherwise.
    not_applicable for financial institutions."""
    V = "altman_z.v1"
    APPL = ["manufacturer", "general", "utility"]
    if sector.is_financial:
        return _na("altman_z", V, as_of, "financial_institution", APPL)
    ca, cl = store.latest_instant("current_assets"), store.latest_instant("current_liabilities")
    ta, tl = store.latest_instant("total_assets"), store.latest_instant("total_liabilities")
    re_, ebit = store.latest_instant("retained_earnings"), store.latest_annual("operating_income")
    sales, eq = store.latest_annual("revenue"), store.latest_instant("equity")
    sh = store.latest_instant("shares_outstanding")
    missing, _ = _need({"current_assets": ca, "current_liabilities": cl,
                        "total_assets": ta, "total_liabilities": tl,
                        "retained_earnings": re_, "operating_income": ebit})
    if missing or not ta.fact.value or not tl.fact.value:
        return _unavailable("altman_z", V, as_of, missing or ["total_assets/liabilities zero"])
    wc_ta = (ca.fact.value - cl.fact.value) / ta.fact.value
    re_ta = re_.fact.value / ta.fact.value
    ebit_ta = ebit.fact.value / ta.fact.value
    inputs = _collect(ca, cl, ta, tl, re_, ebit, sales, eq, sh)

    price = (price_provider.close_on_or_before(ticker, as_of)
             if price_provider else None)
    use_original = (is_manufacturer_sic(sector.sic) and price is not None
                    and sh is not None and sales is not None)
    if use_original:
        mve = price * sh.fact.value
        z = (1.2 * wc_ta + 1.4 * re_ta + 3.3 * ebit_ta
             + 0.6 * (mve / tl.fact.value)
             + 1.0 * (sales.fact.value / ta.fact.value))
        zone = "distress" if z < 1.81 else "safe" if z > 2.99 else "grey"
        variant, extra = "Z", {"mve": mve, "price_used": price}
    else:
        if eq is None:
            return _unavailable("altman_z", V, as_of, ["equity (for Z'' variant)"])
        z = (6.56 * wc_ta + 3.26 * re_ta + 6.72 * ebit_ta
             + 1.05 * (eq.fact.value / tl.fact.value))
        zone = "distress" if z < 1.1 else "safe" if z > 2.6 else "grey"
        variant, extra = "Z_double_prime", {}
    comps = {"variant": variant, "wc_ta": round(wc_ta, 4), "re_ta": round(re_ta, 4),
             "ebit_ta": round(ebit_ta, 4), **extra}
    return _res("altman_z", V, as_of, status=MetricStatus.COMPUTED,
                value=round(z, 4), zone_or_flag=zone, components=comps,
                inputs_used=inputs, sector_applicability=APPL)


def beneish_m(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    """8-ratio Beneish M-score. ALWAYS confidence=low; corroborating flag only."""
    V = "beneish_m.v1"
    APPL = ["general", "manufacturer"]
    if sector.is_financial:
        return _na("beneish_m", V, as_of, "financial_institution", APPL)
    concepts = ["receivables", "revenue", "gross_profit", "current_assets",
                "ppe_net", "total_assets", "dep_amort", "sga", "lt_debt",
                "current_liabilities", "net_income", "cfo"]
    pairs, missing, inputs = {}, [], []
    for c in concepts:
        p = (store.instant_pair(c) if c in
             ("receivables", "current_assets", "ppe_net", "total_assets",
              "lt_debt", "current_liabilities")
             else store.yoy_pair(c))
        if p is None:
            missing.append(f"{c} (2 fiscal years)")
        else:
            pairs[c] = p
            inputs += _collect(*p)
    if missing:
        return _unavailable("beneish_m", V, as_of, missing)

    def cur(c): return pairs[c][0].fact.value
    def pri(c): return pairs[c][1].fact.value
    try:
        dsri = (cur("receivables") / cur("revenue")) / (pri("receivables") / pri("revenue"))
        gm_c = cur("gross_profit") / cur("revenue"); gm_p = pri("gross_profit") / pri("revenue")
        gmi = gm_p / gm_c
        aq = lambda t, ca, ppe: 1 - (ca + ppe) / t
        aqi = (aq(cur("total_assets"), cur("current_assets"), cur("ppe_net"))
               / aq(pri("total_assets"), pri("current_assets"), pri("ppe_net")))
        sgi = cur("revenue") / pri("revenue")
        dep_rate = lambda d, ppe: d / (d + ppe)
        depi = dep_rate(pri("dep_amort"), pri("ppe_net")) / dep_rate(cur("dep_amort"), cur("ppe_net"))
        sgai = (cur("sga") / cur("revenue")) / (pri("sga") / pri("revenue"))
        lvgi = ((cur("lt_debt") + cur("current_liabilities")) / cur("total_assets")) / \
               ((pri("lt_debt") + pri("current_liabilities")) / pri("total_assets"))
        tata = (cur("net_income") - cur("cfo")) / cur("total_assets")
    except ZeroDivisionError:
        return _unavailable("beneish_m", V, as_of, ["zero denominator in ratio inputs"])
    m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    flag = "elevated_manipulation_risk" if m > -1.78 else "not_flagged"
    comps = {k: round(v, 4) for k, v in dict(DSRI=dsri, GMI=gmi, AQI=aqi, SGI=sgi,
             DEPI=depi, SGAI=sgai, LVGI=lvgi, TATA=tata).items()}
    return _res("beneish_m", V, as_of, status=MetricStatus.COMPUTED,
                value=round(m, 4), zone_or_flag=flag, components=comps,
                inputs_used=inputs, sector_applicability=APPL, confidence="low")


def earnings_quality(store: FactStore, sector: SectorInfo, as_of: str) -> MetricResult:
    V = "earnings_quality.v1"
    ni, cf = store.yoy_pair("net_income"), store.yoy_pair("cfo")
    if ni is None or cf is None:
        return _unavailable("earnings_quality", V, as_of, ["net_income/cfo pairs"])
    comps: dict = {"cfo_over_ni_current":
                   None if ni[0].fact.value == 0
                   else round(cf[0].fact.value / ni[0].fact.value, 4)}
    inputs = _collect(*ni, *cf)
    ar, rv = store.instant_pair("receivables"), store.yoy_pair("revenue")
    if ar and rv and all(x.fact.value for x in rv):
        dso_c = 365 * ar[0].fact.value / rv[0].fact.value
        dso_p = 365 * ar[1].fact.value / rv[1].fact.value
        comps["dso_current"], comps["dso_prior"] = round(dso_c, 2), round(dso_p, 2)
        comps["dso_rising"] = dso_c > dso_p * 1.10
        inputs += _collect(*ar)
    inv = store.instant_pair("inventory")
    if inv and rv and inv[1].fact.value and rv[1].fact.value:
        inv_g = (inv[0].fact.value - inv[1].fact.value) / inv[1].fact.value
        rev_g = (rv[0].fact.value - rv[1].fact.value) / abs(rv[1].fact.value)
        comps["inventory_growth_gap"] = round(inv_g - rev_g, 4)
        inputs += _collect(*inv)
    elif sector.is_financial:
        comps["inventory_note"] = "not_applicable_financial_institution"
    return _res("earnings_quality", V, as_of, status=MetricStatus.COMPUTED,
                components=comps, inputs_used=inputs,
                sector_applicability=["universal (inventory component conditional)"])


# --------------------------------------------------- price-dependent set --
def _market_cap(store, ticker, as_of, pp) -> Optional[tuple[float, float, ResolvedFact]]:
    sh = store.latest_instant("shares_outstanding")
    if sh is None or pp is None:
        return None
    price = pp.close_on_or_before(ticker, as_of)
    if price is None:
        return None
    return price * sh.fact.value, price, sh


def valuation_percentile(store: FactStore, sector: SectorInfo, as_of: str, *,
                         ticker: str, price_provider: Optional[PriceProvider],
                         multiple: str) -> MetricResult:
    """Percentile of the current multiple vs the issuer's own fiscal-year-end
    history (needs >=3 historical points). multiple in {'pe','ev_ebitda','p_fcf'}."""
    V = f"valuation_percentile.{multiple}.v1"
    name = f"valuation_pct_{multiple}"
    if sector.is_financial and multiple in ("ev_ebitda", "p_fcf"):
        return _na(name, V, as_of, "financial_institution", ["general"])
    mc = _market_cap(store, ticker, as_of, price_provider)
    if mc is None:
        return _unavailable(name, V, as_of, ["price or shares_outstanding"])
    mcap, price, sh = mc

    def denom_at(idx: int) -> Optional[float]:
        ann = {
            "pe": store.annual("net_income", 7),
            "ev_ebitda": store.annual("operating_income", 7),
            "p_fcf": store.annual("cfo", 7),
        }[multiple]
        if idx >= len(ann):
            return None
        v = ann[idx].fact.value
        if multiple == "ev_ebitda":
            da = store.annual("dep_amort", 7)
            if idx < len(da):
                v += da[idx].fact.value
        if multiple == "p_fcf":
            cx = store.annual("capex", 7)
            if idx < len(cx):
                v -= cx[idx].fact.value
        return v

    ann_dates = [r.fact.end for r in store.annual(
        {"pe": "net_income", "ev_ebitda": "operating_income", "p_fcf": "cfo"}[multiple], 7)]
    lt = store.latest_instant("lt_debt"); st = store.latest_instant("st_debt")
    cash = store.latest_instant("cash")
    net_debt = ((_val(lt) or 0.0) + (_val(st) or 0.0) - (_val(cash) or 0.0))

    def multiple_value(numer_mcap: float, d: Optional[float]) -> Optional[float]:
        if d is None or d <= 0:
            return None
        n = numer_mcap + net_debt if multiple == "ev_ebitda" else numer_mcap
        return n / d

    current = multiple_value(mcap, denom_at(0))
    if current is None:
        return _unavailable(name, V, as_of, [f"non-positive denominator for {multiple}"])
    history: list[float] = []
    for i, d_end in enumerate(ann_dates[1:], start=1):
        p = price_provider.close_on_or_before(ticker, d_end)
        if p is None:
            continue
        mv = multiple_value(p * sh.fact.value, denom_at(i))
        if mv is not None:
            history.append(mv)
    if len(history) < 3:
        return _unavailable(name, V, as_of,
                            [f"insufficient_history ({len(history)} points, need 3)"])
    below = sum(1 for h in history if h < current)
    pct = 100.0 * below / len(history)
    return _res(name, V, as_of, status=MetricStatus.COMPUTED, value=round(pct, 1),
                components={"current_multiple": round(current, 3),
                            "history_points": len(history),
                            "history_median": round(sorted(history)[len(history)//2], 3)},
                inputs_used=_collect(sh, lt, st, cash),
                sector_applicability=["general"], confidence="medium")


def fcf_yield(store, sector, as_of, *, ticker, price_provider) -> MetricResult:
    V = "fcf_yield.v1"
    if sector.is_financial:
        return _na("fcf_yield", V, as_of, "financial_institution", ["general"])
    mc = _market_cap(store, ticker, as_of, price_provider)
    cf, cx = store.latest_annual("cfo"), store.latest_annual("capex")
    if mc is None or cf is None:
        return _unavailable("fcf_yield", V, as_of,
                            ["market_cap"] if mc is None else ["cfo"])
    mcap = mc[0]
    fcf = cf.fact.value - (_val(cx) or 0.0)
    return _res("fcf_yield", V, as_of, status=MetricStatus.COMPUTED,
                value=round(fcf / mcap, 6),
                components={"fcf": fcf, "market_cap": mcap},
                inputs_used=_collect(cf, cx, mc[2]),
                sector_applicability=["general"])


def peg(store, sector, as_of, *, ticker, price_provider) -> MetricResult:
    V = "peg.v1"
    mc = _market_cap(store, ticker, as_of, price_provider)
    pair = store.yoy_pair("net_income")
    if mc is None or pair is None:
        return _unavailable("peg", V, as_of, ["market_cap or net_income pair"])
    cur, prior = pair
    if cur.fact.value <= 0 or prior.fact.value <= 0:
        return _na("peg", V, as_of, "negative_eps_or_base", ["general"])
    growth_pct = 100.0 * (cur.fact.value - prior.fact.value) / prior.fact.value
    if growth_pct <= 0:
        return _na("peg", V, as_of, "non_positive_growth", ["general"])
    pe = mc[0] / cur.fact.value
    return _res("peg", V, as_of, status=MetricStatus.COMPUTED,
                value=round(pe / growth_pct, 4),
                components={"pe": round(pe, 3), "eps_growth_pct": round(growth_pct, 3)},
                inputs_used=_collect(cur, prior, mc[2]),
                sector_applicability=["general"], confidence="medium")


def graham_number(store, sector, as_of) -> MetricResult:
    V = "graham_number.v1"
    ni, eq = store.latest_annual("net_income"), store.latest_instant("equity")
    sh = store.latest_instant("shares_outstanding")
    missing, _ = _need({"net_income": ni, "equity": eq, "shares_outstanding": sh})
    if missing:
        return _unavailable("graham_number", V, as_of, missing)
    if not sh.fact.value:
        return _unavailable("graham_number", V, as_of, ["shares zero"])
    eps, bvps = ni.fact.value / sh.fact.value, eq.fact.value / sh.fact.value
    if eps <= 0 or bvps <= 0:
        return _na("graham_number", V, as_of, "negative_eps_or_bvps", ["general"])
    return _res("graham_number", V, as_of, status=MetricStatus.COMPUTED,
                value=round(math.sqrt(22.5 * eps * bvps), 4),
                components={"eps": round(eps, 4), "bvps": round(bvps, 4)},
                inputs_used=_collect(ni, eq, sh),
                sector_applicability=["general"], confidence="low")


# ------------------------------------------------------ portfolio metrics --
def position_metrics(holding: Holding, price: Optional[float],
                     portfolio_market_value: Optional[float], as_of: str) -> MetricResult:
    V = "position_metrics.v1"
    if not holding.owned:
        return _na("position_metrics", V, as_of, "watch_only_record", ["owned"])
    if price is None or holding.shares is None:
        return _unavailable("position_metrics", V, as_of, ["price or shares"])
    mv = price * holding.shares
    comps: dict = {"market_value": round(mv, 2)}
    if portfolio_market_value:
        comps["weight_pct"] = round(100.0 * mv / portfolio_market_value, 3)
        if holding.target_weight_pct:
            comps["weight_over_target"] = round(
                comps["weight_pct"] / holding.target_weight_pct, 3)
    if holding.cost_basis:
        comps["unrealized_pl_pct"] = round(
            100.0 * (price - holding.cost_basis) / holding.cost_basis, 3)
    return _res("position_metrics", V, as_of, status=MetricStatus.COMPUTED,
                components=comps, sector_applicability=["owned"])


def rebalance_check(current_weight_pct: Optional[float],
                    target_weight_pct: Optional[float], as_of: str) -> MetricResult:
    """5/25 bands: fires on drift >=5 absolute points OR >=25% relative."""
    V = "rebalance_check.v1"
    if current_weight_pct is None or target_weight_pct in (None, 0):
        return _unavailable("rebalance_check", V, as_of, ["weights"])
    drift = current_weight_pct - target_weight_pct
    rel = abs(drift) / target_weight_pct
    fires = abs(drift) >= 5.0 or rel >= 0.25
    return _res("rebalance_check", V, as_of, status=MetricStatus.COMPUTED,
                value=1.0 if fires else 0.0, zone_or_flag="fires" if fires else "within_bands",
                components={"drift_abs_pts": round(drift, 3), "drift_rel": round(rel, 4)},
                sector_applicability=["owned"])


# ------------------------------------------------------------ entry point --
def compute_all(store: FactStore, sector: SectorInfo, *, ticker: str,
                price_provider: Optional[PriceProvider], as_of: str,
                holding: Optional[Holding] = None,
                portfolio_market_value: Optional[float] = None) -> MetricsBundle:
    """The ONLY metrics entry point the pipeline may call."""
    b = MetricsBundle()
    b.results["revenue_growth"] = revenue_growth(store, sector, as_of)
    b.results["net_income_trend"] = net_income_trend(store, sector, as_of)
    b.results["cfo_trend"] = cfo_trend(store, sector, as_of)
    b.results["liquidity_basics"] = liquidity_basics(store, sector, as_of)
    b.results["share_count_change"] = share_count_change(store, sector, as_of)
    b.results["simple_leverage"] = simple_leverage(store, sector, as_of)
    b.results["piotroski_f"] = piotroski_f(store, sector, as_of)
    b.results["altman_z"] = altman_z(store, sector, as_of, ticker=ticker,
                                     price_provider=price_provider)
    b.results["beneish_m"] = beneish_m(store, sector, as_of)
    b.results["earnings_quality"] = earnings_quality(store, sector, as_of)
    for mult in ("pe", "ev_ebitda", "p_fcf"):
        b.valuations.append(valuation_percentile(
            store, sector, as_of, ticker=ticker,
            price_provider=price_provider, multiple=mult))
    b.results["fcf_yield"] = fcf_yield(store, sector, as_of, ticker=ticker,
                                       price_provider=price_provider)
    b.results["peg"] = peg(store, sector, as_of, ticker=ticker,
                           price_provider=price_provider)
    b.results["graham_number"] = graham_number(store, sector, as_of)
    if holding is not None:
        price = (price_provider.close_on_or_before(ticker, as_of)
                 if price_provider else None)
        b.results["position_metrics"] = position_metrics(
            holding, price, portfolio_market_value, as_of)
        pm = b.results["position_metrics"].components
        b.results["rebalance_check"] = rebalance_check(
            pm.get("weight_pct", holding.current_weight_pct),
            holding.target_weight_pct, as_of)
    return b
```

---

## FILE: `src/finwatch/signals/matrix.py`

```python
"""Deterministic signal decision matrix. TIER 1 — do not modify.

Pure function: evaluate(record, extraction, impact, metrics) -> Decision.
No I/O. The verifier's V3 re-runs this function to audit any P3 output, so any
change here is a breaking change to the trust layer.

Precedence (CLAUDE.md §13.1):
  M0 ownership gate -> insufficiency-of-reading check -> M1 document-level
  critical red flags (ZERO metrics required) -> M2 thesis broken ->
  per-rule-gated M4/M6/M7 -> M8 default HOLD -> M5 concentration cap
  (monotone, toward caution only, applied AFTER the base).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from finwatch.core.types import CAUTION_ORDER, CRITICAL_DOC_FLAGS, POSTURE_MAP
from finwatch.metrics.envelope import MetricsBundle


class Record(BaseModel):
    ticker: str
    owned: bool
    current_weight_pct: Optional[float] = None
    target_weight_pct: Optional[float] = None
    unrealized_pl_pct: Optional[float] = None
    thesis: Optional[str] = None


class ExtractionSummary(BaseModel):
    red_flag_codes: list[str] = Field(default_factory=list)  # confirmed CRITICAL_DOC_FLAGS only
    has_red_flags: bool = False               # ANY red flag on the filing (incl. non-critical)
    extraction_confidence: str = "high"      # high|medium|low
    gaps: list[str] = Field(default_factory=list)


class ImpactSummary(BaseModel):
    thesis_verdict: str = "not_assessable"   # intact|weakened|broken|not_assessable
    net_direction: str = "unclear"           # positive|negative|neutral|unclear
    guidance_direction: str = "none_stated"  # raised|maintained|lowered|withdrawn|initiated|none_stated


class Decision(BaseModel):
    signal: str
    posture: Optional[str]
    rules_fired: list[str] = Field(default_factory=list)
    rules_skipped: list[dict] = Field(default_factory=list)  # {"rule","reason"}
    caps_applied: list[str] = Field(default_factory=list)
    data_notes: list[str] = Field(default_factory=list)
    escalation: Optional[dict] = None  # {"from","to","justification"} — engine-applied


def cap_toward_caution(signal: str, floor: str) -> str:
    """Return the more cautious of (signal, floor). Caution = lower index."""
    return signal if CAUTION_ORDER.index(signal) <= CAUTION_ORDER.index(floor) else floor


def _scaled_f(metrics: MetricsBundle) -> Optional[int]:
    r = metrics.get("piotroski_f")
    if not (r and r.computed):
        return None
    s = r.components.get("score_scaled_9")
    return int(s) if s is not None else None


def _altman_zone(metrics: MetricsBundle) -> Optional[str]:
    r = metrics.get("altman_z")
    return r.zone_or_flag if (r and r.computed) else None


def _solvency_bad_if_available(metrics: MetricsBundle) -> bool:
    """Uses solvency metrics ONLY when computed+applicable; absence -> False."""
    zone = _altman_zone(metrics)
    if zone in ("distress", "grey"):
        return True
    f9 = _scaled_f(metrics)
    return f9 is not None and f9 <= 3


def _computed_valuations(metrics: MetricsBundle):
    return [v for v in metrics.valuations if v.computed]


def _finalize(signal: str, fired, skipped, caps, notes,
              escalation=None) -> Decision:
    return Decision(signal=signal, posture=POSTURE_MAP.get(signal),
                    rules_fired=fired, rules_skipped=skipped,
                    caps_applied=caps, data_notes=notes, escalation=escalation)


def evaluate(record: Record, extraction: ExtractionSummary,
             impact: ImpactSummary, metrics: MetricsBundle) -> Decision:
    fired: list[str] = []
    skipped: list[dict] = []
    caps: list[str] = []
    notes: list[str] = []

    # ---- M0 OWNERSHIP / MODE GATE ---------------------------------------
    if not record.owned:
        return Decision(signal="NOT_APPLICABLE_WATCHLIST", posture=None,
                        rules_fired=["M0"])

    # ---- M1 DOCUMENT-LEVEL CRITICAL RED FLAGS (zero metrics required) ----
    if set(extraction.red_flag_codes) & CRITICAL_DOC_FLAGS:
        hit = sorted(set(extraction.red_flag_codes) & CRITICAL_DOC_FLAGS)
        return _finalize("STRONG_REVIEW_SELL", ["M1"] + [f"M1:{h}" for h in hit],
                         skipped, caps, notes)

    # ---- COULD-NOT-READ GATE (after M1 by design) ------------------------
    if extraction.extraction_confidence == "low" and extraction.gaps:
        return Decision(signal="INSUFFICIENT_DATA",
                        posture=POSTURE_MAP["INSUFFICIENT_DATA"],
                        rules_fired=["R_READ"],
                        data_notes=[f"gap:{g}" for g in extraction.gaps])

    # ---- M2 THESIS BROKEN (no metrics required) --------------------------
    if impact.thesis_verdict == "broken":
        if _solvency_bad_if_available(metrics):
            return _finalize("STRONG_REVIEW_SELL", ["M2", "M2a"], skipped, caps, notes)
        return _apply_caps(record, metrics, "TRIM", ["M2"], skipped, caps, notes)

    base: Optional[str] = None

    # ---- M4 SOLVENCY DETERIORATION [gate: altman + piotroski computed] ---
    zone, f9 = _altman_zone(metrics), _scaled_f(metrics)
    if zone is not None and f9 is not None:
        if zone == "distress" and f9 <= 3 and impact.net_direction == "negative":
            base = "STRONG_REVIEW_SELL"
            fired += ["M4"]
    else:
        skipped.append({"rule": "M4", "reason": _gate_reason(metrics)})

    # ---- M6 RICH + DETERIORATING [gate: >=2 valuation percentiles] -------
    if base is None:
        vals = _computed_valuations(metrics)
        if len(vals) >= 2:
            rich = sum(1 for v in vals if (v.value if v.value is not None else 0) >= 90) >= 2
            deteriorating = ((f9 is not None and f9 <= 4)
                             or impact.guidance_direction in ("lowered", "withdrawn"))
            if rich and deteriorating:
                base = "TRIM"
                fired += ["M6"]
        else:
            skipped.append({"rule": "M6",
                            "reason": f"valuation percentiles computed={len(vals)}, need 2"})

    # ---- M7 ACCUMULATE GATE ----------------------------------------------
    if base is None:
        m7_reason = _m7_gate_reason(record, extraction, impact, metrics, f9, zone)
        if m7_reason is None:
            base = "ACCUMULATE"
            fired += ["M7"]
        else:
            skipped.append({"rule": "M7", "reason": m7_reason})

    # ---- M8 DEFAULT --------------------------------------------------------
    if base is None:
        base = "HOLD"
        fired += ["M8"]

    return _apply_caps(record, metrics, base, fired, skipped, caps, notes)


def _apply_caps(record, metrics, base, fired, skipped, caps, notes) -> Decision:
    # ---- M5 CONCENTRATION CAP — monotone, toward caution only -------------
    w, t = record.current_weight_pct, record.target_weight_pct
    if w is not None:
        rc = metrics.get("rebalance_check")
        # M5 is a CONCENTRATION cap: it may only fire on OVER-weight positions. The
        # rebalance_check flag fires on absolute drift in either direction, so it is gated
        # on w > t here — an underweight position that merely drifted below target must
        # never be capped toward caution.
        breach = (w > 15.0
                  or (t not in (None, 0) and w >= 1.5 * t)
                  or (t is not None and w > t
                      and bool(rc and rc.computed and rc.zone_or_flag == "fires")))
        if breach:
            capped = cap_toward_caution(base, "TRIM")
            if capped != base:
                caps.append("M5")
            if "M5" not in fired:
                fired = fired + ["M5"]
            base = capped
    else:
        skipped.append({"rule": "M5", "reason": "weights unavailable"})
    return _finalize(base, fired, skipped, caps, notes)


def _m7_gate_reason(record, extraction, impact, metrics, f9, zone) -> Optional[str]:
    """None -> M7 passes. Otherwise the skip/ineligibility reason."""
    # No accumulation into ANY red flag (CLAUDE.md §13.1 M7 'and not extraction.red_flags').
    # red_flag_codes is critical-only (those already fired M1 above); has_red_flags carries
    # the non-critical flags (e.g. a HIGH covenant breach) that must still block ACCUMULATE.
    if extraction.has_red_flags:
        return "red_flags_present"
    if record.thesis is None:
        return "no_thesis_provided"
    if impact.thesis_verdict != "intact":
        return f"thesis_verdict={impact.thesis_verdict}"
    if f9 is None or zone is None:
        return "piotroski/altman not computed or not applicable"
    if f9 < 7:
        return f"piotroski_scaled={f9} < 7"
    if zone != "safe":
        return f"altman_zone={zone}"
    vals = _computed_valuations(metrics)
    if len(vals) < 2:
        return "insufficient valuation percentiles"
    if sum(1 for v in vals if (v.value if v.value is not None else 100) <= 40) < 2:
        return "valuation not <=40th percentile on 2 multiples"
    if record.current_weight_pct is None or record.target_weight_pct is None:
        return "weights unavailable"
    if record.current_weight_pct >= record.target_weight_pct:
        return "at or above target weight"
    pl = record.unrealized_pl_pct
    if pl is not None and pl <= -20.0:
        pf = metrics.get("piotroski_f")
        comps = pf.components if pf else {}
        if not (f9 >= 6 and comps.get("f3_delta_roa_positive") is True
                and comps.get("f8_gross_margin_improved") is True):
            return "averaging_down_guard: P/L <= -20% without fundamental confirmation"
    return None


def _gate_reason(metrics: MetricsBundle) -> str:
    parts = []
    for name in ("altman_z", "piotroski_f"):
        r = metrics.get(name)
        if r is None:
            parts.append(f"{name}: absent")
        elif not r.computed:
            parts.append(f"{name}: {r.status.value}"
                         + (f" ({r.not_applicable_reason})"
                            if r.not_applicable_reason else ""))
    return "; ".join(parts) or "unknown"


def apply_escalation(decision: Decision, to_signal: str,
                     justification: str) -> Decision:
    """Engine-applied one-notch escalation TOWARD CAUTION only (P3 may request)."""
    cur, tgt = CAUTION_ORDER.index(decision.signal), CAUTION_ORDER.index(to_signal)
    if tgt != cur - 1:
        raise ValueError("escalation must be exactly one notch toward caution")
    d = decision.model_copy(deep=True)
    d.escalation = {"from": decision.signal, "to": to_signal,
                    "justification": justification}
    d.signal = to_signal
    d.posture = POSTURE_MAP.get(to_signal)
    return d
```

---

## FILE: `src/finwatch/verify/checks.py`

```python
"""Deterministic verifier — the compile pass. TIER 1 — do not modify.

V1 numeric provenance · V2 accounting identities (applicability-aware) ·
V3 rule-logic re-derivation · V4 citation integrity · V5 schema & hygiene.
The verifier NEVER edits content. It reports; the pipeline acts.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from finwatch.core.types import DISCLAIMER, FORBIDDEN_VOCABULARY, POSTURE_MAP, SectorInfo
from finwatch.metrics.envelope import MetricsBundle
from finwatch.signals.matrix import (Decision, ExtractionSummary, ImpactSummary,
                                     Record, evaluate)
from finwatch.xbrl.normalize import FactStore


class CheckResult(BaseModel):
    check_id: str
    verdict: str            # pass | fail | warn | skipped_not_applicable
    severity: str           # blocking | warning | info
    detail: str = ""


class EvidenceClaim(BaseModel):
    claim_id: str
    accession_number: str
    section_key: str
    char_start: int
    char_end: int
    snippet: str
    text_sha256: Optional[str] = None


class VerifyBundle(BaseModel):
    rendered_text: str
    metrics: MetricsBundle
    fact_store_values: list[float] = Field(default_factory=list)  # numeric XBRL leaves
    evidence_claims: list[EvidenceClaim] = Field(default_factory=list)
    section_texts: dict[str, str] = Field(default_factory=dict)   # f"{accn}:{section_key}"
    # V3 inputs (present when a P3 decision exists):
    decision: Optional[Decision] = None
    record: Optional[Record] = None
    extraction: Optional[ExtractionSummary] = None
    impact: Optional[ImpactSummary] = None
    # V5:
    trade_action: Any = None
    disclaimer_text: Optional[str] = None


class VerificationReport(BaseModel):
    verdict: str            # PASS | FAIL | PASS_WITH_WARNINGS
    results: list[CheckResult]

    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.verdict == "fail"
                and r.severity == "blocking"]


# ============================================================ V1 — numbers ==
_NUM = re.compile(
    r"(?<![\w.])"                       # not inside identifiers/decimals
    r"(?P<lead_neg>-)?"                 # leading minus sign (the (?<![\w.]) above keeps
                                        # ranges like '5-10' out: the '-' after a digit fails)
    r"(?P<neg>\()?"
    r"(?P<cur>\$)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"\)?"
    r"(?:\s*(?P<suf>billion|million|thousand|bn|mn|k|b|m)\b)?"
    r"(?P<pct>\s?%)?",
    re.IGNORECASE,
)
_SCALE = {"billion": 1e9, "bn": 1e9, "b": 1e9,
          "million": 1e6, "mn": 1e6, "m": 1e6,
          "thousand": 1e3, "k": 1e3}
_WHITELIST_AFTER = re.compile(r"^\s*-\s?[KQkq]\b")          # 10-K / 8-K / 10-Q
_WHITELIST_BEFORE = re.compile(
    r"(Item\s|Rule\s|§\s?|M(?=\d$)|V(?=\d$)|F(?=\d$)|c_|claim_|accession|"
    r"CIK\s?|phase\s|v(?=\d))", re.IGNORECASE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class NumberToken(BaseModel):
    raw: str
    value: float
    tolerance: float
    position: int


def extract_number_tokens(text: str) -> list[NumberToken]:
    tokens: list[NumberToken] = []
    for m in _NUM.finditer(text):
        s, e = m.start(), m.end()
        raw = text[s:e]
        # whitelists ------------------------------------------------------
        if _ISO_DATE.match(text[max(0, s - 5):e + 6].strip("() ")):
            if _ISO_DATE.search(text[max(0, s - 5):e + 6]):
                continue
        if _WHITELIST_AFTER.match(text[e:e + 4]):
            continue                                    # form names 10-K etc.
        before = text[max(0, s - 12):s]
        if _WHITELIST_BEFORE.search(before.strip()):
            continue                                    # Item 2.02, rule ids, claim ids
        num = float(m.group("num").replace(",", "") + (m.group("dec") or ""))
        if (m.group("suf") is None and m.group("cur") is None
                and m.group("pct") is None and m.group("dec") is None
                and 1900 <= num <= 2100):
            continue                                    # bare years
        scale = _SCALE.get((m.group("suf") or "").lower(), 1.0)
        value = num * scale
        if m.group("neg") or m.group("lead_neg"):
            value = -value
        dec_places = len(m.group("dec")) - 1 if m.group("dec") else 0
        tol = 0.5 * (10 ** -dec_places) * scale
        tokens.append(NumberToken(raw=raw, value=value,
                                  tolerance=max(tol, abs(value) * 1e-9),
                                  position=s))
    return tokens


def _candidates(bundle: VerifyBundle) -> list[float]:
    out = list(bundle.fact_store_values)
    for r in bundle.metrics.all_results():
        out.extend(r.numeric_leaves())
    for c in bundle.evidence_claims:
        for t in extract_number_tokens(c.snippet):
            out.append(t.value)
    return out


def _matches(tok: NumberToken, cands: list[float]) -> bool:
    for c in cands:
        for scaled in (c, c / 1e3, c / 1e6, c / 1e9, c * 100.0):  # % re-expression
            if abs(tok.value - scaled) <= tok.tolerance * 1.0001:
                return True
        if abs(c) > 0 and abs(tok.value - c) / abs(c) <= 5e-4:
            return True
    return False


def check_v1_numeric_provenance(bundle: VerifyBundle) -> list[CheckResult]:
    cands = _candidates(bundle)
    out: list[CheckResult] = []
    for tok in extract_number_tokens(bundle.rendered_text):
        if not _matches(tok, cands):
            out.append(CheckResult(
                check_id="V1", verdict="fail", severity="blocking",
                detail=f"orphan number '{tok.raw}' at pos {tok.position}"))
    if not out:
        out.append(CheckResult(check_id="V1", verdict="pass",
                               severity="blocking", detail="all numbers matched"))
    return out


# ====================================================== V2 — identities ====
def check_v2_identities(store: FactStore, sector: SectorInfo) -> list[CheckResult]:
    out: list[CheckResult] = []

    def latest(c):
        r = store.latest_instant(c)
        return None if r is None else r.fact.value

    a, l, e = latest("total_assets"), latest("total_liabilities"), latest("equity")
    if None not in (a, l, e) and a:
        ok = abs(a - (l + e)) <= 0.005 * abs(a)
        out.append(CheckResult(check_id="V2a",
                               verdict="pass" if ok else "fail",
                               severity="blocking",
                               detail=f"A={a} L+E={l + e}"))
    else:
        out.append(CheckResult(check_id="V2a", verdict="skipped_not_applicable",
                               severity="info", detail="concept(s) unresolved"))

    # V2b cash tie-out: ΔBS cash vs CF net change (fx already inside the
    # 'including exchange rate effect' tag when that tag resolved).
    cash_pair = store.instant_pair("cash")
    chg = store.latest_annual("cash_change")
    if cash_pair and chg:
        delta = cash_pair[0].fact.value - cash_pair[1].fact.value
        ok = abs(delta - chg.fact.value) <= max(0.01 * abs(delta), 1.0)
        out.append(CheckResult(check_id="V2b",
                               verdict="pass" if ok else "fail",
                               severity="blocking",
                               detail=f"ΔBS={delta} CF={chg.fact.value}"))
    else:
        out.append(CheckResult(check_id="V2b", verdict="skipped_not_applicable",
                               severity="info", detail="pair or cash_change missing"))

    if sector.is_financial:
        out.append(CheckResult(check_id="V2c", verdict="skipped_not_applicable",
                               severity="info", detail="financial issuer"))
    else:
        rev = store.latest_annual("revenue")
        gp = store.latest_annual("gross_profit")
        oi = store.latest_annual("operating_income")
        if rev and gp and oi:
            ok = rev.fact.value >= gp.fact.value >= oi.fact.value
            out.append(CheckResult(check_id="V2c",
                                   verdict="pass" if ok else "fail",
                                   severity="blocking",
                                   detail=f"rev={rev.fact.value} gp={gp.fact.value} "
                                          f"oi={oi.fact.value}"))
        else:
            out.append(CheckResult(check_id="V2c",
                                   verdict="skipped_not_applicable",
                                   severity="info", detail="line item(s) missing"))

    out.append(CheckResult(check_id="V2d", verdict="skipped_not_applicable",
                           severity="info",
                           detail="segment dimensions not ingested (companyfacts)"))
    return out


# ================================================= V3 — rule re-derivation ==
def check_v3_rederivation(bundle: VerifyBundle) -> list[CheckResult]:
    if bundle.decision is None:
        return [CheckResult(check_id="V3", verdict="skipped_not_applicable",
                            severity="info", detail="no P3 decision in bundle")]
    if None in (bundle.record, bundle.extraction, bundle.impact):
        return [CheckResult(check_id="V3", verdict="fail", severity="blocking",
                            detail="decision present but inputs missing")]
    redo = evaluate(bundle.record, bundle.extraction, bundle.impact, bundle.metrics)
    d = bundle.decision
    expected_signal = redo.signal
    if d.escalation:
        frm, to = d.escalation.get("from"), d.escalation.get("to")
        from finwatch.core.types import CAUTION_ORDER
        if (frm != redo.signal
                or CAUTION_ORDER.index(to) != CAUTION_ORDER.index(frm) - 1):
            return [CheckResult(check_id="V3", verdict="fail",
                                severity="blocking",
                                detail=f"invalid escalation {frm}->{to} "
                                       f"(engine base {redo.signal})")]
        expected_signal = to
    # Full-decision re-derivation (CLAUDE.md §14 V3): posture, signal, rules_fired,
    # rules_skipped, and caps must all match a fresh evaluate() — escalation aside.
    expected_posture = POSTURE_MAP.get(expected_signal)
    mismatches = []
    if d.signal != expected_signal:
        mismatches.append(f"signal {d.signal} != {expected_signal}")
    if d.posture != expected_posture:
        mismatches.append(f"posture {d.posture} != {expected_posture}")
    if sorted(set(d.rules_fired) - {"ESC"}) != sorted(set(redo.rules_fired)):
        mismatches.append(f"rules_fired {d.rules_fired} != {redo.rules_fired}")
    if d.rules_skipped != redo.rules_skipped:
        mismatches.append(f"rules_skipped {d.rules_skipped} != {redo.rules_skipped}")
    if d.caps_applied != redo.caps_applied:
        mismatches.append(f"caps {d.caps_applied} != {redo.caps_applied}")
    if mismatches:
        return [CheckResult(check_id="V3", verdict="fail", severity="blocking",
                            detail="; ".join(mismatches))]
    return [CheckResult(check_id="V3", verdict="pass", severity="blocking",
                        detail="re-derivation exact match")]


# ================================================== V4 — citation integrity ==
def check_v4_citations(bundle: VerifyBundle) -> list[CheckResult]:
    out: list[CheckResult] = []
    for c in bundle.evidence_claims:
        key = f"{c.accession_number}:{c.section_key}"
        text = bundle.section_texts.get(key)
        if text is None:
            out.append(CheckResult(check_id="V4", verdict="fail",
                                   severity="blocking",
                                   detail=f"{c.claim_id}: section {key} not provided"))
            continue
        if c.text_sha256 and hashlib.sha256(
                text.encode()).hexdigest() != c.text_sha256:
            out.append(CheckResult(check_id="V4", verdict="warn",
                                   severity="warning",
                                   detail=f"{c.claim_id}: section hash drift"))
        span = text[c.char_start:c.char_end]
        if c.snippet in span:
            continue
        if c.snippet in text:
            out.append(CheckResult(check_id="V4", verdict="warn",
                                   severity="warning",
                                   detail=f"{c.claim_id}: snippet found outside "
                                          f"declared span (offset drift)"))
        else:
            out.append(CheckResult(check_id="V4", verdict="fail",
                                   severity="blocking",
                                   detail=f"{c.claim_id}: snippet not verbatim in section"))
    if not out:
        out.append(CheckResult(check_id="V4", verdict="pass",
                               severity="blocking", detail="all citations verbatim"))
    return out


# ===================================================== V5 — schema/hygiene ==
_PRICE_TARGET = re.compile(
    r"(price\s+target|target\s+price|will\s+(reach|hit)|"
    r"\$\d+(\.\d+)?\s*(PT\b|target\b|price\s+target))", re.IGNORECASE)


def check_v5_hygiene(bundle: VerifyBundle) -> list[CheckResult]:
    out: list[CheckResult] = []
    text_l = bundle.rendered_text.lower()
    for w in FORBIDDEN_VOCABULARY:
        if w in text_l:
            out.append(CheckResult(check_id="V5", verdict="fail",
                                   severity="blocking",
                                   detail=f"forbidden vocabulary: '{w}'"))
    if _PRICE_TARGET.search(bundle.rendered_text):
        out.append(CheckResult(check_id="V5", verdict="fail",
                               severity="blocking", detail="price-target language"))
    if bundle.trade_action is not None:
        out.append(CheckResult(check_id="V5", verdict="fail",
                               severity="blocking",
                               detail="trade_action must be null in default mode"))
    if bundle.disclaimer_text is not None and bundle.disclaimer_text != DISCLAIMER:
        out.append(CheckResult(check_id="V5", verdict="fail",
                               severity="blocking", detail="disclaimer not verbatim"))
    if not out:
        out.append(CheckResult(check_id="V5", verdict="pass",
                               severity="blocking", detail="hygiene clean"))
    return out


# ================================================================= runner ==
def run_all(bundle: VerifyBundle, store: Optional[FactStore] = None,
            sector: Optional[SectorInfo] = None) -> VerificationReport:
    results: list[CheckResult] = []
    results += check_v1_numeric_provenance(bundle)
    if store is not None and sector is not None:
        results += check_v2_identities(store, sector)
    results += check_v3_rederivation(bundle)
    results += check_v4_citations(bundle)
    results += check_v5_hygiene(bundle)
    blocking_fail = any(r.verdict == "fail" and r.severity == "blocking"
                        for r in results)
    warns = any(r.verdict == "warn" for r in results)
    verdict = ("FAIL" if blocking_fail
               else "PASS_WITH_WARNINGS" if warns else "PASS")
    return VerificationReport(verdict=verdict, results=results)
```

---

## FILE: `tests/test_signals_matrix.py`

```python
"""Executable spec of the decision matrix. TIER 1 — do not modify.
Self-contained: no network, no DB, no LLM."""
from __future__ import annotations

import pytest

from finwatch.core.types import CAUTION_ORDER, MetricStatus
from finwatch.metrics.envelope import MetricResult, MetricsBundle
from finwatch.signals.matrix import (Decision, ExtractionSummary, ImpactSummary,
                                     Record, apply_escalation, cap_toward_caution,
                                     evaluate)

AS_OF = "2026-07-03"


def mr(metric, status=MetricStatus.COMPUTED, value=None, zone=None,
       components=None, reason=None) -> MetricResult:
    return MetricResult(metric=metric, status=status, value=value,
                        zone_or_flag=zone, components=components or {},
                        not_applicable_reason=reason,
                        formula_version=f"{metric}.v1", as_of=AS_OF)


def bundle(*, f_scaled=None, z_zone=None, z_status=MetricStatus.COMPUTED,
           f_status=MetricStatus.COMPUTED, valuations=(), rebalance=None,
           f_components=None) -> MetricsBundle:
    b = MetricsBundle()
    if f_scaled is not None or f_status != MetricStatus.COMPUTED:
        comps = dict(f_components or {})
        if f_scaled is not None:
            comps["score_scaled_9"] = f_scaled
        b.results["piotroski_f"] = mr("piotroski_f", f_status,
                                      value=float(f_scaled or 0), components=comps)
    if z_zone is not None or z_status != MetricStatus.COMPUTED:
        b.results["altman_z"] = mr("altman_z", z_status, zone=z_zone,
                                   reason="financial_institution"
                                   if z_status == MetricStatus.NOT_APPLICABLE else None)
    for pct in valuations:
        b.valuations.append(mr("valuation_pct_pe", value=pct))
    if rebalance is not None:
        b.results["rebalance_check"] = mr("rebalance_check",
                                          zone="fires" if rebalance else "within_bands",
                                          value=1.0 if rebalance else 0.0)
    return b


def rec(**kw) -> Record:
    base = dict(ticker="TEST", owned=True, current_weight_pct=5.0,
                target_weight_pct=10.0, thesis="growth thesis")
    base.update(kw)
    return Record(**base)


CLEAN = ExtractionSummary()
NEUTRAL = ImpactSummary(thesis_verdict="intact", net_direction="neutral")


# ---- M0 -------------------------------------------------------------------
def test_watch_only_gate():
    d = evaluate(rec(owned=False), CLEAN, NEUTRAL, bundle())
    assert d.signal == "NOT_APPLICABLE_WATCHLIST" and d.posture is None


# ---- M1 fires with ZERO metrics (the bug the redesign fixed) ---------------
def test_critical_flag_fires_with_no_metrics_at_all():
    ext = ExtractionSummary(red_flag_codes=["going_concern"],
                            extraction_confidence="low", gaps=["mdna missing"])
    d = evaluate(rec(), ext, ImpactSummary(), MetricsBundle())
    assert d.signal == "STRONG_REVIEW_SELL"
    assert "M1" in d.rules_fired


def test_insufficient_data_only_when_unreadable_and_no_flags():
    ext = ExtractionSummary(extraction_confidence="low", gaps=["all sections"])
    d = evaluate(rec(), ext, ImpactSummary(), MetricsBundle())
    assert d.signal == "INSUFFICIENT_DATA"


def test_missing_metrics_alone_yield_hold_not_insufficient():
    d = evaluate(rec(), CLEAN, NEUTRAL, MetricsBundle())
    assert d.signal == "HOLD" and d.posture == "monitor"
    skipped = {s["rule"] for s in d.rules_skipped}
    assert {"M4", "M6", "M7"} <= skipped


# ---- M2 ---------------------------------------------------------------------
def test_thesis_broken_solvent_is_trim():
    d = evaluate(rec(), CLEAN, ImpactSummary(thesis_verdict="broken"),
                 bundle(f_scaled=8, z_zone="safe"))
    assert d.signal == "TRIM" and "M2" in d.rules_fired


def test_thesis_broken_with_bad_solvency_is_srs():
    d = evaluate(rec(), CLEAN, ImpactSummary(thesis_verdict="broken"),
                 bundle(f_scaled=2, z_zone="grey"))
    assert d.signal == "STRONG_REVIEW_SELL" and "M2a" in d.rules_fired


# ---- per-rule gates: a bank must not break the matrix ----------------------
def test_bank_not_applicable_altman_skips_m4_and_holds():
    b = bundle(f_scaled=5, z_status=MetricStatus.NOT_APPLICABLE)
    d = evaluate(rec(), CLEAN, NEUTRAL, b)
    assert d.signal == "HOLD"
    assert any(s["rule"] == "M4" and "not_applicable" in s["reason"]
               for s in d.rules_skipped)


# ---- M4 ----------------------------------------------------------------------
def test_solvency_deterioration_srs():
    b = bundle(f_scaled=2, z_zone="distress")
    d = evaluate(rec(), CLEAN,
                 ImpactSummary(thesis_verdict="intact", net_direction="negative"), b)
    assert d.signal == "STRONG_REVIEW_SELL" and "M4" in d.rules_fired


# ---- M6 ----------------------------------------------------------------------
def test_rich_and_deteriorating_trims():
    b = bundle(f_scaled=3, z_zone="safe", valuations=(95, 92, 50))
    d = evaluate(rec(), CLEAN, NEUTRAL, b)
    assert d.signal == "TRIM" and "M6" in d.rules_fired


def test_guidance_withdrawn_counts_as_deteriorating():
    b = bundle(f_scaled=8, z_zone="safe", valuations=(95, 92))
    d = evaluate(rec(), CLEAN,
                 ImpactSummary(thesis_verdict="intact", net_direction="neutral",
                               guidance_direction="withdrawn"), b)
    assert d.signal == "TRIM"


# ---- M7 ----------------------------------------------------------------------
def good_accumulate_bundle():
    return bundle(f_scaled=8, z_zone="safe", valuations=(20, 30, 35))


def test_accumulate_all_gates_pass():
    d = evaluate(rec(current_weight_pct=5.0, target_weight_pct=10.0),
                 CLEAN, NEUTRAL, good_accumulate_bundle())
    assert d.signal == "ACCUMULATE" and "M7" in d.rules_fired


def test_no_thesis_makes_m7_ineligible_but_never_blocks_hold():
    d = evaluate(rec(thesis=None), CLEAN,
                 ImpactSummary(thesis_verdict="not_assessable"),
                 good_accumulate_bundle())
    assert d.signal == "HOLD"
    assert any(s["rule"] == "M7" and s["reason"] == "no_thesis_provided"
               for s in d.rules_skipped)


def test_averaging_down_guard_blocks_accumulate():
    d = evaluate(rec(unrealized_pl_pct=-35.0), CLEAN, NEUTRAL,
                 good_accumulate_bundle())
    assert d.signal == "HOLD"
    assert any("averaging_down_guard" in s["reason"] for s in d.rules_skipped)


# ---- M5 cap: monotone toward caution only ------------------------------------
def test_concentration_caps_accumulate_to_trim():
    d = evaluate(rec(current_weight_pct=20.0, target_weight_pct=10.0),
                 CLEAN, NEUTRAL, good_accumulate_bundle())
    assert d.signal == "TRIM" and "M5" in d.caps_applied


def test_concentration_never_softens_srs():
    ext = ExtractionSummary(red_flag_codes=["item_4_02_non_reliance"])
    d = evaluate(rec(current_weight_pct=20.0), ext, NEUTRAL, bundle())
    assert d.signal == "STRONG_REVIEW_SELL"          # M1 short-circuits before caps


@pytest.mark.parametrize("base,floor,expect", [
    ("ACCUMULATE", "TRIM", "TRIM"), ("HOLD", "TRIM", "TRIM"),
    ("TRIM", "TRIM", "TRIM"), ("STRONG_REVIEW_SELL", "TRIM", "STRONG_REVIEW_SELL")])
def test_cap_monotone(base, floor, expect):
    assert cap_toward_caution(base, floor) == expect


# ---- escalation ----------------------------------------------------------------
def test_escalation_one_notch_toward_caution_only():
    d = evaluate(rec(), CLEAN, NEUTRAL, bundle(f_scaled=8, z_zone="safe"))
    assert d.signal == "HOLD"
    e = apply_escalation(d, "TRIM", "governance concerns")
    assert e.signal == "TRIM" and e.escalation["from"] == "HOLD"
    with pytest.raises(ValueError):
        apply_escalation(d, "ACCUMULATE", "nope")      # toward aggression
    with pytest.raises(ValueError):
        apply_escalation(d, "STRONG_REVIEW_SELL", "two notches")


# ---- property: caps never make the outcome less cautious ------------------------
def test_property_final_never_less_cautious_than_base():
    for w in (None, 5.0, 12.0, 20.0):
        for vals in ((), (95, 92), (20, 30, 35)):
            b = bundle(f_scaled=8, z_zone="safe", valuations=vals)
            d = evaluate(rec(current_weight_pct=w), CLEAN, NEUTRAL, b)
            if "M5" in d.caps_applied:
                assert CAUTION_ORDER.index(d.signal) <= CAUTION_ORDER.index("TRIM")
```

---

## FILE: `tests/test_verifier_mutations.py`

```python
"""Mutation battery — the verifier's Definition of Done. TIER 1 — do not modify.
Builds a known-good bundle, then seeds five corruptions; each must FAIL on the
correct check id, and the clean bundle must PASS."""
from __future__ import annotations

import hashlib

from finwatch.core.types import DISCLAIMER, MetricStatus, SectorClass, SectorInfo
from finwatch.metrics.envelope import MetricResult, MetricsBundle
from finwatch.signals.matrix import ExtractionSummary, ImpactSummary, Record, evaluate
from finwatch.verify.checks import (EvidenceClaim, VerifyBundle,
                                    check_v2_identities, run_all)
from finwatch.xbrl.normalize import Fact, FactStore

AS_OF = "2026-07-03"
SECTION = ("Net revenue was $1,234.5 million for the quarter. "
           "The company recorded an impairment of $87.0 million.")
KEY = "0000000000-26-000001:mdna"


def make_store(assets=1000.0, liab=600.0, equity=400.0) -> FactStore:
    def inst(tag, val, end="2026-03-31", filed="2026-05-01"):
        return Fact(taxonomy="us-gaap", tag=tag, unit="USD", value=val,
                    end=end, filed=filed, accn="a1")
    return FactStore([
        inst("Assets", assets), inst("Liabilities", liab),
        inst("StockholdersEquity", equity),
    ])


def make_bundle(rendered=None, snippet="$1,234.5 million",
                char=(16, 31)) -> VerifyBundle:
    metrics = MetricsBundle()
    metrics.results["revenue_growth"] = MetricResult(
        metric="revenue_growth", status=MetricStatus.COMPUTED, value=0.12,
        components={"yoy": 0.12}, formula_version="revenue_growth.v1", as_of=AS_OF)
    record = Record(ticker="TEST", owned=True, current_weight_pct=5.0,
                    target_weight_pct=10.0, thesis="t")
    ext, imp = ExtractionSummary(), ImpactSummary(thesis_verdict="intact",
                                                  net_direction="neutral")
    decision = evaluate(record, ext, imp, metrics)
    rendered = rendered or ("Revenue grew 0.12 year over year; the filing cites "
                            "$1,234.5 million of net revenue.")
    return VerifyBundle(
        rendered_text=rendered,
        metrics=metrics,
        fact_store_values=[1_234.5e6, 87.0e6],
        evidence_claims=[EvidenceClaim(
            claim_id="c_0001", accession_number="0000000000-26-000001",
            section_key="mdna", char_start=char[0], char_end=char[1],
            snippet=snippet,
            text_sha256=hashlib.sha256(SECTION.encode()).hexdigest())],
        section_texts={KEY: SECTION},
        decision=decision, record=record, extraction=ext, impact=imp,
        trade_action=None, disclaimer_text=DISCLAIMER)


def failing_ids(report):
    return {r.check_id for r in report.results
            if r.verdict == "fail" and r.severity == "blocking"}


def test_clean_bundle_passes():
    r = run_all(make_bundle(), make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert r.verdict in ("PASS", "PASS_WITH_WARNINGS"), [x.detail for x in r.results]
    assert not failing_ids(r)


def test_mutation_a_flipped_digit_fails_v1():
    b = make_bundle(rendered="Revenue grew 0.12; the filing cites "
                             "$1,334.5 million of net revenue.")
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V1" in failing_ids(r)


def test_mutation_b_broken_identity_fails_v2a():
    results = check_v2_identities(make_store(assets=1000.0, liab=600.0, equity=300.0),
                                  SectorInfo(SectorClass.GENERAL, False))
    assert any(c.check_id == "V2a" and c.verdict == "fail" for c in results)


def test_mutation_c_altered_snippet_fails_v4():
    b = make_bundle(snippet="$1,234.5 billion")       # one word altered
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V4" in failing_ids(r)


def test_mutation_d_changed_rule_id_fails_v3():
    b = make_bundle()
    b.decision = b.decision.model_copy(update={"rules_fired": ["M4"]})
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V3" in failing_ids(r)


def test_mutation_e_price_target_language_fails_v5():
    b = make_bundle(rendered="Revenue grew 0.12; we see a price target of $50.")
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V5" in failing_ids(r)


def test_bank_income_ordering_is_skipped_not_failed():
    results = check_v2_identities(make_store(),
                                  SectorInfo(SectorClass.FINANCIAL, True))
    v2c = [c for c in results if c.check_id == "V2c"]
    assert v2c and v2c[0].verdict == "skipped_not_applicable"
```

---

## Final note to the building agent

After transcription, wire the world around this core:
- `pipeline/adapters.py` converts P1/P2 JSON → `ExtractionSummary` / `ImpactSummary`
  (map red-flag findings to the `CRITICAL_DOC_FLAGS` codes in `core/types.py`).
- `ingest/stooq.py` implements `PriceProvider`; persist to the `prices` table.
- Persist every `MetricResult` via `model_dump_json()` into `computations`, and every
  `Decision` + inputs into `signal_shadow_log` — V3 depends on faithful storage.
- The digest renderer must pass its final markdown through `verify.checks.run_all`
  before writing to disk.

These eight files are the trust layer. Everything else is plumbing. Build the plumbing well,
but build it *around* this.

— end of CORE_CODE.md —
