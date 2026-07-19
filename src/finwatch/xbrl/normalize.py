"""XBRL normalization layer over SEC companyfacts JSON. Trust-critical (test-guarded): edit with care, keep the spec tests green.

Design (per CLAUDE.md §8):
  * Concept resolution via priority-ordered tag lists; first tag with usable
    facts wins; the tag actually used is recorded on every resolved fact. Revenue
    is the exception: because issuers migrate revenue tags over time, it resolves
    to the FRESHEST coherent tag (fails closed on tags that disagree on the newest
    period), never splicing values across tags.
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

from pydantic import BaseModel, FiniteFloat

from finwatch.metrics.envelope import InputUsed
from finwatch.xbrl.companyfacts import FactRejection, iter_companyfacts

#AS is this concept map below enough
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


def _in_window(window):
    lo, hi = window
    return lambda f: (not f.is_instant and f.duration_days is not None
                      and lo <= f.duration_days <= hi)


class Fact(BaseModel):
    taxonomy: str
    tag: str
    unit: str
    value: FiniteFloat
    decimals: Optional[str] = None
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
        return (self.taxonomy, self.tag, self.unit, self.start or "", self.end or "")


class ResolvedFact(BaseModel):
    """A Fact annotated with the concept it satisfied."""
    concept: str
    fact: Fact

    def to_input_used(self) -> InputUsed:
        f = self.fact
        return InputUsed(
            concept=self.concept, tag=f.tag, taxonomy=f.taxonomy,
            value=f.value, unit_ref=f.unit, decimals=f.decimals,
            period_start=f.start,
            period_end=None if f.is_instant else f.end,
            instant=f.end if f.is_instant else None,
            accession_number=f.accn,
        )


class FactStore:
    def __init__(
        self, facts: Iterable[Fact], *, rejected: Iterable[FactRejection] | None = None
    ) -> None:
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
        # Rejected (unusable-value) entries indexed by the exact (taxonomy, tag, unit)
        # a series accessor resolves against, so `_newest_rejected_after` can fail closed
        # when the datapoint we would present as "current" may not be the true newest.
        self._rejected_ends: dict[tuple[str, str, str], list[str | None]] = {}
        for r in (rejected or ()):
            self._rejected_ends.setdefault((r.taxonomy, r.tag, r.unit), []).append(r.end)

    # -- construction -----------------------------------------------------
    @classmethod
    def from_companyfacts(cls, cf_json: dict) -> "FactStore":
        valid, rejected = iter_companyfacts(cf_json)
        out: list[Fact] = []
        for entry in valid:
            e = entry.entry
            out.append(Fact(
                taxonomy=entry.taxonomy, tag=entry.tag, unit=entry.unit,
                value=entry.value,
                decimals=None if e.get("decimals") is None else str(e.get("decimals")),
                start=e.get("start"), end=e.get("end"),
                fy=e.get("fy"), fp=e.get("fp"), form=e.get("form"),
                accn=e.get("accn"), filed=e.get("filed"),
                frame=e.get("frame"),
            ))
        return cls(out, rejected=rejected)

    def _newest_rejected_after(
        self, taxo: str, tag: str, unit: str, newest_end: str | None
    ) -> bool:
        """True if a rejected value for this (taxo, tag, unit) could be at least as
        recent as ``newest_end`` — i.e. the current-period datapoint may have been the
        one we dropped. A missing/unparseable date (on either side) is treated as
        possibly-current: we fail closed rather than risk presenting an older period as
        current (the stale-looks-current hazard)."""
        ends = self._rejected_ends.get((taxo, tag, unit))
        if not ends:
            return False
        if not newest_end:
            return True
        try:
            newest = date.fromisoformat(newest_end)
        except (TypeError, ValueError):
            return True
        for end in ends:
            if not end:
                return True
            try:
                if date.fromisoformat(end) > newest:
                    return True
            except (TypeError, ValueError):
                return True
        return False

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
    # Resolution is PER-ACCESSOR: each accessor scans the concept's priority tags and uses
    # the FIRST tag that yields facts of the requested period type. A primary tag carrying
    # only quarterly data therefore falls through to an annual fallback tag for annual()
    # (F11), instead of resolving the tag once globally and then filtering to nothing.
    def annual(self, concept: str, n: int = 6) -> list[ResolvedFact]:
        """Annual-duration facts, newest first, one per period end."""
        return self._series(concept, _in_window(_ANNUAL), n)

    def quarterly(self, concept: str, n: int = 10) -> list[ResolvedFact]:
        return self._series(concept, _in_window(_QUARTER), n)

    def instant(self, concept: str, n: int = 6) -> list[ResolvedFact]:
        """Instant facts, newest first, one per date."""
        return self._series(concept, lambda f: f.is_instant, n)

    def _freshest_coherent_tag(self, concept: str, keep) -> Optional[tuple[str, str]]:
        """Pick the candidate tag whose newest fact of the requested period type is the
        MOST RECENT, so an abandoned tag's stale value never masquerades as current.

        Companies migrate reporting tags over time — e.g. dropping
        ``RevenueFromContractWithCustomerExcludingAssessedTax`` for ``Revenues`` — which
        the "first priority tag with any facts wins" rule silently pins to the abandoned
        (stale) tag. Priority order still breaks a tie on the SAME newest period-end, but
        ONLY when the tied tags AGREE on that period's value; a genuine disagreement (a
        total ``Revenues`` vs a Topic 606 contract-revenue subset for the same period) is
        a conflict and fails closed (→ unavailable) rather than silently choosing a
        meaning. Values are never spliced across tags — a single tag supplies the series."""
        newest_by_tag: dict[tuple[str, str], tuple[str, float]] = {}
        for taxo, tag in self._tags_for(concept):
            eligible = [
                f for f in self._by_tag.get((taxo, tag), ()) if keep(f) and f.end
            ]
            if not eligible:
                continue
            top = max(eligible, key=lambda f: f.end)
            newest_by_tag[(taxo, tag)] = (top.end, top.value)
        if not newest_by_tag:
            return None
        latest_end = max(end for end, _v in newest_by_tag.values())
        at_latest = {k: v for k, (end, v) in newest_by_tag.items() if end == latest_end}
        if len(set(at_latest.values())) > 1:
            return None  # tags disagree on the newest period's value → fail closed
        priority = {tag: i for i, tag in enumerate(self._tags_for(concept))}
        return min(at_latest, key=lambda tag: priority[tag])

    def _resolution_order(self, concept: str, keep) -> list[tuple[str, str]]:
        # Revenue tags migrate across filings, so choose the freshest coherent tag rather
        # than the first-with-any-facts. Every other concept keeps its priority-ordered
        # fallback (a primary tag carrying only the wrong period type must fall through).
        if concept != "revenue":
            return self._tags_for(concept)
        chosen = self._freshest_coherent_tag(concept, keep)
        return [chosen] if chosen is not None else []

    def _series(self, concept: str, keep, n: int) -> list[ResolvedFact]:
        """Deduped (by period-end, newest first) series from the resolved tag whose facts
        pass `keep`. Unit choice is deterministic: headline monetary concepts prefer USD,
        share counts prefer shares, and a sole alternate unit is accepted. Multiple
        non-preferred units are ambiguous and fail closed instead of depending on JSON order."""
        for taxo, tag in self._resolution_order(concept, keep):
            eligible = [f for f in self._by_tag.get((taxo, tag), ()) if keep(f)]
            if not eligible:
                continue
            by_unit: dict[str, list[Fact]] = {}
            for fact in eligible:
                by_unit.setdefault(fact.unit, []).append(fact)
            preferred = "shares" if concept == "shares_outstanding" else "USD"
            if preferred in by_unit:
                selected = by_unit[preferred]
            elif len(by_unit) == 1:
                selected = next(iter(by_unit.values()))
            else:
                return []
            rows = [ResolvedFact(concept=concept, fact=f) for f in selected]
            rows.sort(key=lambda r: r.fact.end or "", reverse=True)
            seen: set = set()
            out: list[ResolvedFact] = []
            for r in rows:
                if r.fact.end in seen:
                    continue
                seen.add(r.fact.end)
                out.append(r)
                if len(out) >= n:
                    break
            # Poison guard: if an unusable value for this exact tag+unit could be newer
            # than the newest fact we kept, the true current datapoint may have been
            # dropped — refuse the series (→ unavailable) rather than let an older
            # period masquerade as current.
            if out and self._newest_rejected_after(taxo, tag, out[0].fact.unit,
                                                   out[0].fact.end):
                return []
            return out
        return []

    # -- convenience ---------------------------------------------------------
    def latest_annual(self, concept: str) -> Optional[ResolvedFact]:
        s = self.annual(concept, 1)
        return s[0] if s else None

    def latest_instant(self, concept: str) -> Optional[ResolvedFact]:
        s = self.instant(concept, 1)
        return s[0] if s else None

    def yoy_pair(self, concept: str, kind: str = "annual"
                 ) -> Optional[tuple[ResolvedFact, ResolvedFact]]:
        """(current, ~one-year-prior) pair, annual or same-quarter-last-year.

        Both branches require the two legs to be ~one year apart (330-400 day
        spacing). Consumers (revenue_growth, etc.) label the result "YoY", so a
        fiscal-year change, a transition/stub period, or a missing annual must
        yield ``None`` (→ unavailable) rather than a mislabeled multi-year delta —
        the annual branch previously paired the two newest annuals blindly.
        """
        series = self.annual(concept, 6) if kind == "annual" else self.quarterly(concept, 8)
        if not series:
            return None
        cur = series[0]
        cur_end = date.fromisoformat(cur.fact.end)
        for cand in series[1:]:
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
