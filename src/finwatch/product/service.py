"""Deterministic product assembly over RipplX's verified filing and metric stores."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime

from finwatch.core.types import DISCLAIMER
from finwatch.db.repositories import Company, Computation, Filing, Repo
from finwatch.metrics.envelope import MetricResult
from finwatch.presentation import PresentationService
from finwatch.product.models import (
    AttentionEvent,
    BeforeYouBuyBrief,
    ChangeImpact,
    CompanyProfile,
    EvidenceRef,
    ManagementPromise,
    PeerComparison,
    RiskRadarResult,
    StockImpactSnapshot,
    Thesis,
    ThesisItem,
    ValuationAssumptions,
    ValuationRun,
    ValuationScenario,
)
from finwatch.product.store import ProductStore

VALUATION_FORMULA_VERSION = "reverse_dcf.v2"
_EVENT_TERMS = {
    "restatement": "RESTATEMENT",
    "going concern": "GOING_CONCERN",
    "auditor resign": "AUDITOR_RESIGNATION",
    "guidance withdraw": "GUIDANCE_WITHDRAWAL",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _metric(row: Computation | None) -> MetricResult | None:
    if row is None:
        return None
    try:
        result = MetricResult.model_validate_json(row.result_json)
    except ValueError:
        return None
    if (
        result.metric != row.tool
        or result.status.value != row.status
        or result.formula_version != row.formula_version
        or result.as_of != row.as_of
    ):
        return None
    return result


def _metric_ref(name: str, row: Computation | None) -> list[EvidenceRef]:
    return (
        []
        if row is None or row.id is None
        else [EvidenceRef(kind="metric", reference_id=f"computation:{row.id}:{name}")]
    )


def _risk(
    lens: str,
    status: str,
    code: str,
    explanation: str,
    *,
    metrics: list[str] | None = None,
    evidence: list[EvidenceRef] | None = None,
    period: str | None = None,
    freshness: str | None = None,
) -> RiskRadarResult:
    return RiskRadarResult(
        lens=lens,
        status=status,
        reason_codes=[code],
        explanation=explanation,
        metric_ids=metrics or [],
        evidence=evidence or [],
        comparison_period=period,
        freshness=freshness,
    )


class ProductService:
    """One lean service for the customer loop; no model can alter deterministic states."""

    def __init__(self, repo: Repo, *, user_id: str, now_fn=_now) -> None:
        self.repo = repo
        self.user_id = user_id
        self.store = ProductStore(repo, user_id)
        self.now_fn = now_fn
        self.presentation = PresentationService(repo, user_id=user_id)

    def _company(self, ticker: str) -> Company | None:
        company = self.repo.get_company_by_ticker(ticker)
        if company is None or self.repo.get_user_company(self.user_id, company.cik) is None:
            return None
        return company

    def profile(self, ticker: str) -> CompanyProfile | None:
        company = self._company(ticker)
        return None if company is None else self.store.profile(company, now=self.now_fn())

    def save_profile(self, ticker: str, profile: CompanyProfile) -> CompanyProfile | None:
        company = self._company(ticker)
        if company is None or profile.cik != company.cik or profile.ticker != company.ticker:
            return None
        saved = profile.model_copy(update={"updated_at": self.now_fn()})
        self.store.save_profile(saved)
        return saved

    def _metrics(self, company: Company) -> dict[str, tuple[Computation, MetricResult | None]]:
        return {
            row.tool: (row, _metric(row)) for row in self.repo.latest_computations(company.ticker)
        }

    def _latest_supported(self, company: Company) -> Filing | None:
        return next(
            (
                row
                for row in self.repo.list_filings(company.cik)
                if row.form_type.split("/")[0] in {"10-K", "10-Q", "8-K"}
            ),
            None,
        )

    def risk_radar(self, ticker: str, *, persist: bool = False) -> list[RiskRadarResult] | None:
        company = self._company(ticker)
        if company is None:
            return None
        rows = self._metrics(company)
        latest = self._latest_supported(company)
        results = [
            self._liquidity(rows.get("liquidity_basics")),
            self._leverage(rows.get("simple_leverage")),
            self._cash_conversion(rows.get("cfo_trend"), rows.get("net_income_trend")),
            self._deterioration(rows),
            self._share_count(rows.get("share_count_change")),
            _risk(
                "concentration",
                "unavailable",
                "CONCENTRATION_NOT_STRUCTURED",
                "Customer or supplier concentration is not yet available as a verified structure.",
            ),
            self._filing_events(latest),
        ]
        if persist:
            self._refresh_thesis(company, results)
            self._capture_promises(company, latest)
        results.append(self._thesis_and_promises(company))
        if persist:
            key = latest.accession_number if latest else f"no-filing:{date.today().isoformat()}"
            self.store.save_risks(
                company.cik,
                latest.accession_number if latest else None,
                key,
                json.dumps([row.model_dump(mode="json") for row in results]),
                self.now_fn(),
            )
        return results

    def _refresh_thesis(self, company: Company, risks: list[RiskRadarResult]) -> None:
        profile = self.store.profile(company, now=self.now_fn())
        statuses = {row.lens: row.status for row in risks}
        mapped = {
            "stable": "supported",
            "watch": "weakened",
            "elevated": "broken",
            "unavailable": "unclear",
        }
        items = []
        changed = False
        for item in profile.thesis.items:
            updated = item
            if item.lens and item.status in {
                "confirmed",
                "supported",
                "weakened",
                "broken",
                "unclear",
            }:
                next_status = mapped[statuses.get(item.lens, "unavailable")]
                if next_status != item.status:
                    updated = item.model_copy(update={"status": next_status})
                    changed = True
            items.append(updated)
        if changed:
            self.store.save_profile(
                profile.model_copy(
                    update={"thesis": Thesis(items=items), "updated_at": self.now_fn()}
                )
            )

    def _capture_promises(self, company: Company, filing: Filing | None) -> None:
        if filing is None:
            return
        detail = self.presentation.filing(filing.accession_number)
        if detail is None:
            return
        phrases = ("we expect", "we intend", "we plan", "we target", "we commit")
        now = self.now_fn()
        for finding in detail.filing.findings:
            for evidence in finding.evidence:
                if not any(phrase in evidence.quote.lower() for phrase in phrases):
                    continue
                raw_id = (
                    f"{self.user_id}:{company.cik}:{evidence.accession}:"
                    f"{evidence.section_key}:{evidence.char_start}:{evidence.char_end}"
                )
                self.store.save_promise(
                    company,
                    ManagementPromise(
                        promise_id=hashlib.sha256(raw_id.encode()).hexdigest()[:24],
                        ticker=company.ticker,
                        accession=evidence.accession,
                        section_key=evidence.section_key,
                        char_start=evidence.char_start,
                        char_end=evidence.char_end,
                        section_sha256=evidence.section_sha256,
                        quote=evidence.quote,
                    ),
                    now,
                )

    def _liquidity(self, pair) -> RiskRadarResult:
        row, metric = pair or (None, None)
        evidence = _metric_ref("liquidity_basics", row)
        if metric is None or not metric.computed:
            return _risk(
                "liquidity",
                "unavailable",
                "LIQUIDITY_INPUTS_UNAVAILABLE",
                "Verified cash and debt inputs are unavailable.",
                metrics=["liquidity_basics"],
                evidence=evidence,
            )
        net_debt = metric.components.get("net_debt")
        current_ratio = metric.components.get("current_ratio")
        current_liabilities_uncovered = current_ratio is not None and current_ratio < 1
        net_debt_positive = net_debt is not None and net_debt > 0
        if current_liabilities_uncovered and net_debt_positive:
            return _risk(
                "liquidity",
                "elevated",
                "CURRENT_LIABILITIES_UNCOVERED_WITH_NET_DEBT",
                "Current liabilities exceed current assets while verified net debt is positive.",
                metrics=[metric.metric],
                evidence=evidence,
                freshness=metric.as_of,
            )
        if current_liabilities_uncovered or (current_ratio is None and net_debt_positive):
            return _risk(
                "liquidity",
                "watch",
                "ONE_LIQUIDITY_SIGNAL_WEAK",
                "One verified liquidity signal is weak; review it with leverage and cash flow.",
                metrics=[metric.metric],
                evidence=evidence,
                freshness=metric.as_of,
            )
        return _risk(
            "liquidity",
            "stable",
            "CURRENT_LIABILITIES_COVERED",
            "Current assets cover current liabilities, or verified net cash offsets the gap.",
            metrics=[metric.metric],
            evidence=evidence,
            freshness=metric.as_of,
        )

    def _leverage(self, pair) -> RiskRadarResult:
        row, metric = pair or (None, None)
        evidence = _metric_ref("simple_leverage", row)
        if metric is None or not metric.computed:
            return _risk(
                "leverage",
                "unavailable",
                "LEVERAGE_INPUTS_UNAVAILABLE",
                "A verified leverage proxy is unavailable or not applicable.",
                metrics=["simple_leverage"],
                evidence=evidence,
            )
        leverage = metric.components.get("net_debt_to_ebitda")
        coverage = metric.components.get("interest_coverage")
        if (leverage is not None and leverage > 4) or (coverage is not None and coverage < 2):
            status, code = "elevated", "REFINANCING_PRESSURE"
            explanation = "The leverage proxy or interest coverage indicates refinancing pressure."
        elif leverage is not None and leverage <= 2 and (coverage is None or coverage >= 4):
            status, code = "stable", "LEVERAGE_MANAGEABLE"
            explanation = "The verified leverage proxy is within the stable policy range."
        else:
            status, code = "watch", "LEVERAGE_NEEDS_WATCHING"
            explanation = "The leverage proxy is between the stable and elevated policy ranges."
        return _risk(
            "leverage",
            status,
            code,
            explanation,
            metrics=[metric.metric],
            evidence=evidence,
            freshness=metric.as_of,
        )

    def _cash_conversion(self, cfo_pair, income_pair) -> RiskRadarResult:
        cfo_row, cfo = cfo_pair or (None, None)
        income_row, income = income_pair or (None, None)
        evidence = [
            *_metric_ref("cfo_trend", cfo_row),
            *_metric_ref("net_income_trend", income_row),
        ]
        if not cfo or not income or not cfo.computed or not income.computed:
            return _risk(
                "cash_conversion",
                "unavailable",
                "CASH_CONVERSION_INPUTS_UNAVAILABLE",
                "Comparable operating cash flow and net income are unavailable.",
                metrics=["cfo_trend", "net_income_trend"],
                evidence=evidence,
            )
        cfo_current = cfo.components.get("current")
        income_current = income.components.get("current")
        if not isinstance(cfo_current, (int, float)) or not isinstance(
            income_current, (int, float)
        ):
            return _risk(
                "cash_conversion",
                "unavailable",
                "CASH_CONVERSION_INPUTS_UNAVAILABLE",
                "Comparable current-period inputs are unavailable.",
                metrics=["cfo_trend", "net_income_trend"],
                evidence=evidence,
            )
        if income_current <= 0:
            status, code = (
                ("elevated", "CFO_NEGATIVE")
                if cfo_current < 0
                else ("watch", "EARNINGS_NONPOSITIVE")
            )
        else:
            ratio = cfo_current / income_current
            status, code = (
                ("stable", "CFO_COVERS_INCOME")
                if ratio >= 1
                else ("watch", "CFO_LAGS_INCOME")
                if ratio >= 0.5
                else ("elevated", "ACCRUAL_DIVERGENCE")
            )
        explanation = {
            "stable": "Operating cash flow covers reported net income.",
            "watch": "Cash conversion is positive but trails reported income.",
            "elevated": "Operating cash flow is weak relative to reported income.",
        }[status]
        return _risk(
            "cash_conversion",
            status,
            code,
            explanation,
            metrics=["cfo_trend", "net_income_trend"],
            evidence=evidence,
            freshness=max(cfo.as_of, income.as_of),
        )

    def _deterioration(self, rows) -> RiskRadarResult:
        names = ["revenue_growth", "net_income_trend", "cfo_trend"]
        available = [(name, rows.get(name)) for name in names]
        available = [
            (name, pair) for name, pair in available if pair and pair[1] and pair[1].computed
        ]
        evidence = [ref for name, pair in available for ref in _metric_ref(name, pair[0])]
        if not available:
            return _risk(
                "operating_deterioration",
                "unavailable",
                "TRENDS_UNAVAILABLE",
                "Verified comparable operating trends are unavailable.",
                metrics=names,
            )
        down = [name for name, pair in available if pair[1].deterministic_direction == "down"]
        if len(down) >= 2:
            status, code, text = (
                "elevated",
                "MULTIPLE_TRENDS_DOWN",
                "Multiple verified operating measures declined.",
            )
        elif down:
            status, code, text = (
                "watch",
                "ONE_TREND_DOWN",
                "One verified operating measure declined.",
            )
        else:
            status, code, text = (
                "stable",
                "NO_VERIFIED_DECLINE",
                "No available operating measure shows a rounding-significant decline.",
            )
        return _risk(
            "operating_deterioration",
            status,
            code,
            text,
            metrics=[name for name, _ in available],
            evidence=evidence,
            freshness=max(pair[1].as_of for _, pair in available),
        )

    def _share_count(self, pair) -> RiskRadarResult:
        row, metric = pair or (None, None)
        evidence = _metric_ref("share_count_change", row)
        if metric is None or not metric.computed or metric.value is None:
            return _risk(
                "share_count",
                "unavailable",
                "SHARE_COUNT_UNAVAILABLE",
                "Comparable verified share counts are unavailable.",
                metrics=["share_count_change"],
                evidence=evidence,
            )
        if metric.value > 0.05:
            status, code = "elevated", "SHARE_COUNT_UP_OVER_FIVE_PERCENT"
        elif metric.value > 0.01:
            status, code = "watch", "SHARE_COUNT_UP_OVER_ONE_PERCENT"
        else:
            status, code = "stable", "NO_MATERIAL_SHARE_EXPANSION"
        return _risk(
            "share_count",
            status,
            code,
            "Share-count change is classified from two comparable SEC XBRL points.",
            metrics=[metric.metric],
            evidence=evidence,
            freshness=metric.as_of,
        )

    def _filing_events(self, filing: Filing | None) -> RiskRadarResult:
        if filing is None:
            return _risk(
                "filing_events",
                "unavailable",
                "NO_SUPPORTED_FILING",
                "No supported SEC filing is available.",
            )
        detail = self.presentation.filing(filing.accession_number)
        if detail is None or detail.filing.outcome in {
            "not_analyzed",
            "pipeline_failed",
            "withheld_gate",
        }:
            return _risk(
                "filing_events",
                "unavailable",
                "LATEST_FILING_NOT_VERIFIED",
                "The latest filing has not produced a publishable verified result.",
            )
        findings = detail.filing.findings
        evidence = [
            EvidenceRef(
                kind="filing",
                reference_id=finding.finding_id,
                accession=e.accession,
                section_key=e.section_key,
                char_start=e.char_start,
                char_end=e.char_end,
                quote=e.quote,
                section_sha256=e.section_sha256,
            )
            for finding in findings
            for e in finding.evidence[:1]
        ][:4]
        severe = any(row.severity in {"CRITICAL", "HIGH"} for row in findings)
        review = any(row.severity == "MEDIUM" for row in findings)
        status = "elevated" if severe else "watch" if review else "stable"
        code = (
            "SEVERE_VERIFIED_FILING_CHANGE"
            if severe
            else "REVIEWABLE_VERIFIED_FILING_CHANGE"
            if review
            else "LOW_SEVERITY_VERIFIED_CHANGE"
            if findings
            else "NO_MATERIAL_VERIFIED_CHANGE"
        )
        explanation = (
            "A high-severity verified filing change needs prompt review."
            if severe
            else "A medium-severity verified filing change merits review this week."
            if review
            else "Only low-severity verified filing changes were found."
            if findings
            else "No publishable filing change was classified as material."
        )
        return _risk(
            "filing_events",
            status,
            code,
            explanation,
            evidence=evidence,
            freshness=filing.filed_at[:10],
        )

    def _thesis_and_promises(self, company: Company) -> RiskRadarResult:
        profile = self.store.profile(company, now=self.now_fn())
        promises = self.store.promises(company)
        broken = [row for row in profile.thesis.items if row.status == "broken"]
        missed = [row for row in promises if row.status == "missed"]
        if broken or missed:
            return _risk(
                "thesis_and_promises",
                "elevated",
                "WATCH_CONDITION_OR_COMMITMENT_BROKEN",
                "A saved watch condition is broken or a filing commitment is missed.",
                evidence=[EvidenceRef(kind="thesis", reference_id=row.item_id) for row in broken],
            )
        if any(row.status in {"weakened", "unclear"} for row in profile.thesis.items) or promises:
            return _risk(
                "thesis_and_promises",
                "watch",
                "OPEN_ITEM_NEEDS_REVIEW",
                "A saved watch condition or filing commitment remains open for review.",
            )
        if any(row.status in {"confirmed", "supported"} for row in profile.thesis.items):
            return _risk(
                "thesis_and_promises",
                "stable",
                "CONFIRMED_ITEMS_NOT_WEAKENED",
                "No confirmed watch condition is currently marked weakened or broken.",
            )
        return _risk(
            "thesis_and_promises",
            "unavailable",
            "NO_SAVED_WATCH_CONDITION",
            "No saved condition is required; filing and risk monitoring remains active.",
        )

    def draft_thesis(self, ticker: str) -> Thesis | None:
        risks = self.risk_radar(ticker)
        if risks is None:
            return None
        items: list[ThesisItem] = []
        stable = [row for row in risks if row.status == "stable"][:2]
        weak = [row for row in risks if row.status in {"watch", "elevated"}][:3]
        for row in stable:
            items.append(
                ThesisItem(
                    item_id=f"t{len(items) + 1}",
                    kind="reason",
                    text=row.explanation,
                    lens=row.lens,
                )
            )
        for row in weak:
            items.append(
                ThesisItem(
                    item_id=f"t{len(items) + 1}",
                    kind="risk",
                    text=row.explanation,
                    lens=row.lens,
                )
            )
        for row in risks:
            if row.status == "unavailable" and len(items) < 5:
                items.append(
                    ThesisItem(
                        item_id=f"t{len(items) + 1}",
                        kind="next_evidence",
                        text=f"Resolve the evidence gap for {row.lens.replace('_', ' ')}.",
                        lens=row.lens,
                    )
                )
        return Thesis(items=items)

    def _valuation_inputs(self, company: Company):
        rows = self._metrics(company)
        cfo_row, cfo = rows.get("cfo_trend", (None, None))
        income_row, income = rows.get("net_income_trend", (None, None))
        liq_row, liq = rows.get("liquidity_basics", (None, None))
        shares_row, shares = rows.get("share_count_change", (None, None))
        if not all((cfo and cfo.computed, liq and liq.computed, shares and shares.computed)):
            return None
        cfo_current = cfo.components.get("current")
        share_current = shares.components.get("current")
        net_debt = liq.components.get("net_debt")
        cfo_source = next((row for row in cfo.inputs_used if row.concept == "cfo"), None)
        if not cfo_source or not cfo_source.period_end or not isinstance(cfo_current, (int, float)):
            return None
        capex = next(
            (
                row
                for row in self.repo.list_xbrl_facts(company.cik)
                if row.tag == "PaymentsToAcquirePropertyPlantAndEquipment"
                and row.period_end == cfo_source.period_end
                and row.period_start == cfo_source.period_start
                and row.value is not None
            ),
            None,
        )
        if (
            capex is None
            or not isinstance(share_current, (int, float))
            or share_current <= 0
            or not isinstance(net_debt, (int, float))
        ):
            return None
        free_cash_flow = cfo_current - abs(float(capex.value))
        if free_cash_flow <= 0:
            return None
        refs = [
            *_metric_ref("cfo_trend", cfo_row),
            *_metric_ref("liquidity_basics", liq_row),
            *_metric_ref("share_count_change", shares_row),
            EvidenceRef(
                kind="metric",
                reference_id=f"xbrl_fact:{capex.id}:capex",
                accession=capex.accession_number,
            ),
        ]
        net_income = None
        if income and income.computed and income.as_of == cfo.as_of:
            value = income.components.get("current")
            if isinstance(value, (int, float)):
                net_income = float(value)
                refs.extend(_metric_ref("net_income_trend", income_row))
        return free_cash_flow, net_debt, share_current, net_income, refs

    @staticmethod
    def _dcf_per_share(
        fcf: float, net_debt: float, shares: float, growth: float, discount: float, terminal: float
    ) -> float:
        cash_flows = [fcf * ((1 + growth) ** year) for year in range(1, 6)]
        terminal_value = cash_flows[-1] * (1 + terminal) / (discount - terminal)
        enterprise = sum(
            value / ((1 + discount) ** year) for year, value in enumerate(cash_flows, 1)
        )
        enterprise += terminal_value / ((1 + discount) ** 5)
        return max(0.0, (enterprise - net_debt) / shares)

    def calculate_valuation(
        self, ticker: str, *, price: float, price_as_of: str, assumptions: ValuationAssumptions
    ) -> ValuationRun | None:
        company = self._company(ticker)
        if company is None:
            return None
        created = self.now_fn()
        inputs = self._valuation_inputs(company)
        run_id = uuid.uuid4().hex
        if inputs is None:
            payload = {
                "run_id": run_id,
                "ticker": company.ticker,
                "price": price,
                "price_as_of": price_as_of,
                "status": "unavailable",
                "label": "Unavailable",
                "explanation": (
                    "Required SEC cash-flow, capital-spending, debt, or share inputs "
                    "are unreliable."
                ),
                "assumptions": assumptions.model_dump(mode="json"),
                "scenarios": [],
                "reverse_dcf_growth": None,
                "trailing_pe": None,
                "price_to_fcf": None,
                "fcf_yield": None,
                "inputs": [],
                "formula_version": VALUATION_FORMULA_VERSION,
                "created_at": created,
            }
        else:
            fcf, net_debt, shares, net_income, refs = inputs
            scenario_growth = {
                "conservative": assumptions.conservative_growth,
                "base": assumptions.base_growth,
                "optimistic": assumptions.optimistic_growth,
            }
            scenarios = []
            for name, growth in scenario_growth.items():
                implied = round(
                    self._dcf_per_share(
                        fcf,
                        net_debt,
                        shares,
                        growth,
                        assumptions.discount_rate,
                        assumptions.terminal_growth,
                    ),
                    2,
                )
                scenarios.append(
                    ValuationScenario(
                        name=name,
                        growth=growth,
                        implied_value_per_share=implied,
                        change_percent=round((implied / price - 1) * 100, 1),
                    )
                )
            low, high = -0.50, 1.00
            for _ in range(80):
                mid = (low + high) / 2
                if (
                    self._dcf_per_share(
                        fcf,
                        net_debt,
                        shares,
                        mid,
                        assumptions.discount_rate,
                        assumptions.terminal_growth,
                    )
                    < price
                ):
                    low = mid
                else:
                    high = mid
            reverse = (low + high) / 2
            base = scenarios[1].implied_value_per_share
            fcf_per_share = fcf / shares
            earnings_per_share = net_income / shares if net_income and net_income > 0 else None
            label = (
                "Demanding"
                if price > base * 1.2
                else "Undemanding"
                if price < base * 0.8
                else "Balanced"
            )
            payload = {
                "run_id": run_id,
                "ticker": company.ticker,
                "price": price,
                "price_as_of": price_as_of,
                "status": "computed",
                "label": label,
                "explanation": (
                    f"{label} under these explicit assumptions; this is not a single correct value."
                ),
                "assumptions": assumptions.model_dump(mode="json"),
                "scenarios": [row.model_dump(mode="json") for row in scenarios],
                "reverse_dcf_growth": round(reverse, 6),
                "trailing_pe": (
                    round(price / earnings_per_share, 2) if earnings_per_share else None
                ),
                "price_to_fcf": round(price / fcf_per_share, 2),
                "fcf_yield": round(fcf_per_share / price, 6),
                "inputs": [row.model_dump(mode="json") for row in refs],
                "formula_version": VALUATION_FORMULA_VERSION,
                "created_at": created,
            }
        certificate = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        run = ValuationRun(**payload, certificate_hash=certificate)
        self.store.save_valuation(company, run)
        return run

    @staticmethod
    def _stock_impact(
        *, risks: list[RiskRadarResult], valuation: ValuationRun | None,
        recent_filings: list[dict],
    ) -> StockImpactSnapshot:
        metric_drivers = {
            "revenue_growth": ("revenue", "REVENUE"),
            "net_income_trend": ("earnings", "EARNINGS"),
            "cfo_trend": ("cash_flow", "CASH_FLOW"),
            "share_count_change": ("per_share", "SHARE_COUNT"),
        }
        implications = {
            "revenue": "This can affect future cash flow and operating leverage if it persists.",
            "earnings": "This can affect earnings power and the valuation investors support.",
            "cash_flow": "This can strengthen or weaken the cash flow supporting valuation.",
            "balance_sheet": (
                "This can change financial risk and the discount rate investors require."
            ),
            "per_share": "The per-share effect depends on why the share count changed.",
            "operations": (
                "The stock effect remains uncertain until the operating impact is quantified."
            ),
        }
        changes: list[ChangeImpact] = []
        reasons: list[str] = []
        latest = recent_filings[0] if recent_filings else {}
        for finding in latest.get("findings", [])[:3]:
            metric_id = finding.get("metric_id")
            direction = finding.get("direction")
            if metric_id in metric_drivers:
                driver, code = metric_drivers[metric_id]
                effect = (
                    "uncertain" if driver == "per_share" else
                    "upside" if direction == "up" else
                    "downside" if direction == "down" else
                    "mixed" if direction == "flat" else "uncertain"
                )
                if effect in {"upside", "downside"}:
                    reasons.append(f"VERIFIED_{code}_{direction.upper()}")
            else:
                headline = finding["headline"].lower()
                if any(term in headline for term in ("revenue", "sales")):
                    driver = "revenue"
                elif any(term in headline for term in ("income", "earnings", "margin")):
                    driver = "earnings"
                elif "cash" in headline:
                    driver = "cash_flow"
                elif any(
                    term in headline
                    for term in ("debt", "liquidity", "default", "going concern")
                ):
                    driver = "balance_sheet"
                else:
                    driver = "operations"
                effect = "uncertain"
            evidence = [
                EvidenceRef(
                    kind="filing",
                    reference_id=row["claim_id"],
                    accession=row["accession"],
                    section_key=row["section_key"],
                    char_start=row["char_start"],
                    char_end=row["char_end"],
                    quote=row["quote"],
                    section_sha256=row["section_sha256"],
                )
                for row in finding.get("evidence", [])[:3]
            ]
            changes.append(
                ChangeImpact(
                    finding_id=finding["finding_id"],
                    headline=finding["headline"],
                    driver=driver,
                    effect=effect,
                    implication=implications[driver],
                    evidence=evidence,
                )
            )

        downside = sum(row.effect == "downside" for row in changes)
        upside = sum(row.effect == "upside" for row in changes)
        if any(row.status == "elevated" for row in risks):
            downside += 1
            reasons.append("ELEVATED_DOWNSIDE_RISK")
        if valuation and valuation.status == "computed":
            if valuation.label == "Demanding":
                downside += 1
                reasons.append("DEMANDING_BASE_SCENARIO")
            elif valuation.label == "Undemanding":
                upside += 1
                reasons.append("UNDEMANDING_BASE_SCENARIO")
        pressure = (
            "downside" if downside > upside else
            "upside" if upside > downside else
            "mixed" if downside or upside else "uncertain"
        )
        summary = {
            "downside": "Verified evidence currently creates more downside than upside pressure.",
            "upside": "Verified evidence currently creates more upside than downside pressure.",
            "mixed": "Verified evidence points in both directions under the current assumptions.",
            "uncertain": (
                "There is not enough verified evidence to infer directional stock pressure."
            ),
        }[pressure]
        priced_in = (
            f"At ${valuation.price:.2f}, the saved reverse DCF implies about "
            f"{valuation.reverse_dcf_growth * 100:.1f}% annual five-year FCF growth."
            if valuation
            and valuation.status == "computed"
            and valuation.reverse_dcf_growth is not None
            else "Enter a current price to compare verified cash flow with priced-in expectations."
        )
        watch = next(
            (row.explanation for row in risks if row.status == "elevated"),
            changes[0].implication
            if changes
            else "Analyze the newest filing to establish what changed.",
        )
        return StockImpactSnapshot(
            directional_pressure=pressure,
            summary=summary,
            priced_in=priced_in,
            watch_next=watch,
            reason_codes=list(dict.fromkeys(reasons))[:8],
            changes=changes,
        )

    def peers(self, ticker: str) -> list[PeerComparison] | None:
        company = self._company(ticker)
        if company is None:
            return None
        profile = self.store.profile(company, now=self.now_fn())
        result = []
        for peer in self.store.peers(company, profile):
            metric_view = self.presentation.metrics(peer.ticker, as_of=date.today().isoformat())
            peer_risks = (
                self.risk_radar(peer.ticker)
                if self.repo.get_user_company(self.user_id, peer.cik)
                else None
            )
            result.append(
                PeerComparison(
                    ticker=peer.ticker,
                    name=peer.name,
                    sic_code=peer.sic_code,
                    reason=(
                        "Same SEC SIC classification"
                        if peer.sic_code == company.sic_code
                        else "Manually selected comparison"
                    ),
                    risk_statuses={row.lens: row.status for row in peer_risks or []},
                    metrics={row.metric: row.value for row in metric_view.rows}
                    if metric_view
                    else {},
                )
            )
        return result

    def _business_summary(self, filing: Filing | None):
        if filing is None:
            return None, None
        section = next(
            (
                row
                for row in self.repo.list_filing_sections(filing.accession_number)
                if row.section_key == "business"
            ),
            None,
        )
        if section is None or not section.text.strip():
            return None, None
        clean = " ".join(section.text.split())
        end = min(len(clean), 600)
        for marker in (". ", "? ", "! "):
            found = clean.find(marker, min(120, len(clean)))
            if found != -1:
                end = min(end, found + 1)
        quote = clean[:end]
        start = section.char_start or 0
        return quote, EvidenceRef(
            kind="filing",
            reference_id=f"business:{filing.accession_number}",
            accession=filing.accession_number,
            section_key="business",
            char_start=start,
            char_end=start + len(quote),
            quote=quote,
            section_sha256=section.text_sha256,
        )

    def create_attention_event(self, ticker: str) -> AttentionEvent | None:
        company = self._company(ticker)
        if company is None:
            return None
        filing = self._latest_supported(company)
        # Attention events are investment-facing conclusions. A failed or fully withheld
        # analysis may still expose deterministic metrics elsewhere, but it cannot assert
        # that the filing was routine or material.
        if filing is None or filing.status != "verified":
            return None
        previous_risks = {row.lens: row.status for row in self.store.latest_risks(company.cik)}
        risks = self.risk_radar(company.ticker, persist=True) or []
        detail = self.presentation.filing(filing.accession_number)
        terms = set()
        for finding in detail.filing.findings if detail else []:
            lower = finding.headline.lower()
            terms.update(code for term, code in _EVENT_TERMS.items() if term in lower)
            if finding.severity == "CRITICAL":
                terms.add("CRITICAL_VERIFIED_CHANGE")
        elevated = [row.lens for row in risks if row.status == "elevated"]
        rank = {"unavailable": 0, "stable": 1, "watch": 2, "elevated": 3}
        risk_changes = [
            f"{row.lens}:{previous_risks.get(row.lens, 'unavailable')}->{row.status}"
            for row in risks
            if row.status in {"watch", "elevated"}
            and rank[row.status] > rank[previous_risks.get(row.lens, "unavailable")]
        ]
        profile = self.store.profile(company, now=self.now_fn())
        thesis_impacts = [
            f"{row.item_id}:{row.status}"
            for row in profile.thesis.items
            if row.status in {"weakened", "broken", "unclear"}
        ]
        if any(row.status == "broken" for row in profile.thesis.items):
            terms.add("CONFIRMED_THESIS_BREAK")
        if terms:
            priority = "urgent"
        elif elevated or (detail and detail.filing.findings):
            priority = "this_week"
        else:
            priority = "routine"
        reasons = sorted(terms) or (
            ["DOWNSIDE_RISK_ELEVATED"] if elevated else ["NO_IMPORTANT_VERIFIED_CHANGE"]
        )
        event = AttentionEvent(
            event_key=hashlib.sha256(
                f"{self.user_id}:{company.cik}:{filing.accession_number}".encode()
            ).hexdigest(),
            ticker=company.ticker,
            cik=company.cik,
            accession=filing.accession_number,
            priority=priority,
            reason_codes=reasons,
            risk_changes=risk_changes,
            thesis_impacts=thesis_impacts,
            created_at=self.now_fn(),
        )
        self.store.insert_event(event)
        return next(
            (
                row
                for row in self.store.list_events(cik=company.cik)
                if row.event_key == event.event_key
            ),
            event,
        )

    def before_you_buy(self, ticker: str) -> BeforeYouBuyBrief | None:
        company = self._company(ticker)
        if company is None:
            return None
        filing = self._latest_supported(company)
        as_of = filing.filed_at[:10] if filing else date.today().isoformat()
        risks = self.risk_radar(company.ticker) or []
        summary, summary_ref = self._business_summary(filing)
        profile = self.store.profile(company, now=self.now_fn())
        # Presentation revalidates persisted computations point-in-time. Query the
        # current view while keeping the brief's filing date as its narrative as-of.
        metrics = self.presentation.metrics(
            company.ticker, as_of=date.today().isoformat()
        )
        details = []
        certificates = []
        for row in self.repo.list_filings(company.cik)[:3]:
            detail = self.presentation.filing(row.accession_number)
            if detail is None:
                continue
            details.append(detail.filing.model_dump(mode="json"))
            if detail.certificate_url:
                certificates.append(detail.certificate_url)
        questions = [
            "What creates the biggest verified downside?",
            "What changed in the latest filing?",
            "What does the saved valuation assume?",
            "What evidence would change this view?",
        ]
        questions.extend(
            f"What source could resolve {row.lens.replace('_', ' ')}?"
            for row in risks
            if row.status == "unavailable"
        )
        valuation = self.store.latest_valuation(company)
        impact = self._stock_impact(
            risks=risks,
            valuation=valuation,
            recent_filings=details,
        )
        return BeforeYouBuyBrief(
            ticker=company.ticker,
            cik=company.cik,
            company_name=company.name,
            as_of=as_of,
            attention=self.store.list_events(cik=company.cik, limit=5),
            risks=risks,
            business_summary=summary,
            business_evidence=summary_ref,
            recent_filings=details,
            metrics=metrics.model_dump(mode="json") if metrics else {},
            valuation=valuation,
            impact=impact,
            profile=profile,
            thesis=profile.thesis,
            promises=self.store.promises(company),
            peers=self.peers(company.ticker) or [],
            manual_peer_tickers=[
                peer.ticker
                for cik in profile.peer_ciks
                if (peer := self.repo.get_company(cik)) is not None
            ],
            questions=questions[:8],
            certificate_urls=certificates,
            deep_research=self.store.latest_research_run(company),
            disclaimer=DISCLAIMER,
        )

    def list_events(self) -> list[AttentionEvent]:
        return self.store.list_events()

    def mark_event_read(self, event_id: int) -> bool:
        return self.store.mark_event_read(event_id, self.now_fn())
