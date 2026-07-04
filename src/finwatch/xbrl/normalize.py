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
