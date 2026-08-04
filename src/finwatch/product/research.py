"""Bounded, provider-neutral company research over RipplX's verified stores."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from finwatch.core.text_policy import authored_text_violations
from finwatch.core.types import DISCLAIMER
from finwatch.db.repositories import Company, Filing, FilingSection
from finwatch.llm.prompts import STAGE_COMPANY_RESEARCH, STAGE_COMPANY_RESEARCH_SKEPTIC, load_prompt
from finwatch.llm.router import LLMClient, extract_json
from finwatch.metrics.catalog import MetricId
from finwatch.preprocess.forms import base_form
from finwatch.presentation.projection import load_filing_projection
from finwatch.product.models import (
    CompanyResearchReport,
    EvidenceRef,
    ResearchInsight,
    ResearchObligation,
    ResearchObligationId,
    ResearchObservation,
    ResearchTrace,
    RiskLens,
    research_observation_hash,
)
from finwatch.product.service import ProductService

_STRICT = ConfigDict(extra="forbid")
MAX_TURNS = 6
MAX_TOOLS = 4
COMPILER_VERSION = "company_research_compiler.v1"
OBLIGATIONS: tuple[ResearchObligationId, ...] = (
    "BUSINESS_ECONOMICS",
    "IMPORTANT_CHANGES",
    "FINANCIAL_QUALITY_AND_DOWNSIDE",
    "VALUATION_CONTEXT",
    "PEER_CONTEXT",
    "SOURCE_COVERAGE",
)


class SearchArgs(BaseModel):
    model_config = _STRICT
    queries: list[str] = Field(min_length=1, max_length=3)
    section_keys: list[str] = Field(default_factory=list, max_length=8)
    scope: Literal["current", "prior", "both"] = "current"

    @model_validator(mode="after")
    def bounded_queries(self) -> SearchArgs:
        if any(not query.strip() or len(query) > 80 for query in self.queries):
            raise ValueError("search queries must contain 1-80 characters")
        return self


class FinancialArgs(BaseModel):
    model_config = _STRICT
    metric_ids: list[MetricId] = Field(default_factory=list, max_length=6)
    risk_lenses: list[RiskLens] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def nonempty(self) -> FinancialArgs:
        if not self.metric_ids and not self.risk_lenses:
            raise ValueError("select at least one metric or risk lens")
        return self


class EmptyArgs(BaseModel):
    model_config = _STRICT


class SearchAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["search_filing_sections"]
    arguments: SearchArgs


class ChangesAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["get_verified_changes"]
    arguments: EmptyArgs


class FinancialAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["get_financial_context"]
    arguments: FinancialArgs


class ValuationAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["get_valuation_context"]
    arguments: EmptyArgs


class PeerAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["get_peer_context"]
    arguments: EmptyArgs


class DraftInsight(BaseModel):
    model_config = _STRICT
    insight_id: str = Field(pattern=r"^i[1-5]$")
    category: Literal["business", "change", "financial_quality", "valuation", "peer"]
    headline: str = Field(min_length=1, max_length=180)
    evidence_summary: str = Field(min_length=1, max_length=320)
    driver: str = Field(min_length=1, max_length=180)
    mechanism: Literal[
        "revenue",
        "margin",
        "working_capital",
        "cash_conversion",
        "capital_spending",
        "leverage",
        "liquidity",
        "dilution",
        "discount_rate",
        "uncertain",
    ]
    implication: str = Field(min_length=1, max_length=420)
    scenario: Literal["downside", "upside", "mixed", "neutral"]
    assumptions: list[str] = Field(min_length=1, max_length=2)
    limitations: list[str] = Field(min_length=1, max_length=2)
    observation_ids: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def bounded_list_text(self) -> DraftInsight:
        if any(not value.strip() or len(value) > 240 for value in self.assumptions):
            raise ValueError("assumptions must contain 1-240 characters")
        if any(not value.strip() or len(value) > 240 for value in self.limitations):
            raise ValueError("limitations must contain 1-240 characters")
        return self


class ResearchDraft(BaseModel):
    model_config = _STRICT
    summary: str = Field(min_length=1, max_length=600)
    insights: list[DraftInsight] = Field(default_factory=list, max_length=5)


class SubmitAction(BaseModel):
    model_config = _STRICT
    action: Literal["submit"]
    draft: ResearchDraft


ResearchAction = Annotated[
    SearchAction | ChangesAction | FinancialAction | ValuationAction | PeerAction | SubmitAction,
    Field(union_mode="left_to_right"),
]
_ACTION = TypeAdapter(ResearchAction)


class SkepticObjection(BaseModel):
    model_config = _STRICT
    insight_id: str = Field(pattern=r"^i[1-5]$")
    code: Literal[
        "HYPOTHETICAL_AS_ACTUAL",
        "TEMPORAL_MISMATCH",
        "ENTITY_MISMATCH",
        "MATERIALITY_OVERREACH",
        "METRIC_CONTRADICTION",
        "MISSING_CHANGE_BASIS",
        "LOW_CONFIDENCE",
    ]


class SkepticDecision(BaseModel):
    model_config = _STRICT
    action: Literal["review"]
    objections: list[SkepticObjection] = Field(default_factory=list, max_length=5)


@dataclass(frozen=True)
class HarnessResult:
    report: CompanyResearchReport
    trace: ResearchTrace
    status: Literal["completed", "partial"]


class ResearchHarnessError(RuntimeError):
    """A closed-vocabulary terminal failure; provider text is never retained."""


def _json_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def research_input_hash(service: ProductService, ticker: str) -> str:
    """Hash only persisted, deterministic inputs plus versioned policy identities."""
    company = service._company(ticker)
    if company is None:
        raise ValueError("company not found")
    filing = service._latest_supported(company)
    valuation = service.store.latest_valuation(company)
    peers = service.store.peers(company, service.store.profile(company, now=service.now_fn()))
    _, prompt_version = load_prompt(STAGE_COMPANY_RESEARCH)
    return _json_hash(
        {
            "company": [company.cik, company.ticker],
            "data_cutoff": service.now_fn()[:10],
            "filing": None
            if filing is None
            else [filing.accession_number, filing.raw_sha256, filing.status, filing.processed_at],
            "computations": [
                [
                    row.id,
                    row.tool,
                    row.status,
                    row.formula_version,
                    row.as_of,
                    hashlib.sha256(row.result_json.encode()).hexdigest(),
                ]
                for row in service.repo.latest_computations(company.ticker)
            ],
            "valuation": None
            if valuation is None
            else [valuation.run_id, valuation.certificate_hash, valuation.price_as_of],
            "peers": [
                [
                    peer.cik,
                    peer.ticker,
                    [row.id for row in service.repo.latest_computations(peer.ticker)],
                ]
                for peer in peers
            ],
            "prompt": prompt_version,
            "compiler": COMPILER_VERSION,
        }
    )


class CompanyResearchHarness:
    def __init__(
        self,
        service: ProductService,
        generator: LLMClient,
        skeptic: LLMClient | None = None,
    ) -> None:
        self.service = service
        self.generator = generator
        self.skeptic = skeptic or generator

    def run(self, ticker: str) -> HarnessResult:
        company = self.service._company(ticker)
        if company is None:
            raise ResearchHarnessError("company_not_found")
        filing = self.service._latest_supported(company)
        if filing is None or filing.status != "verified":
            raise ResearchHarnessError("verified_filing_unavailable")
        system, prompt_version = load_prompt(STAGE_COMPANY_RESEARCH)
        observations: dict[str, ResearchObservation] = {}
        cache: dict[str, list[ResearchObservation]] = {}
        calls: list[dict] = []
        draft: ResearchDraft | None = None
        compiler_errors: dict[str, list[str]] = {}
        protected_insights: dict[str, DraftInsight] = {}
        invalid_actions = 0
        repair_used = False
        turns = 0
        terminal_reason = "submitted"

        while turns < MAX_TURNS:
            turns += 1
            user = self._turn_payload(
                company,
                filing,
                observations,
                compiler_errors,
                turns=turns,
                calls=calls,
            )
            try:
                action = _ACTION.validate_python(
                    extract_json(self.generator.complete(system=system, user=user).text)
                )
            except Exception as exc:  # provider failures and malformed output differ
                if isinstance(exc, (ValidationError, ValueError, json.JSONDecodeError)):
                    invalid_actions += 1
                    if invalid_actions < 2:
                        continue
                    # This pass is optional and downstream of an already-verified
                    # company brief. Preserve that deterministic baseline when the
                    # provider cannot follow the research action contract; the trace
                    # still records the exact closed terminal reason.
                    terminal_reason = "malformed_action_breakdown"
                    break
                raise ResearchHarnessError("provider_failed") from None

            if isinstance(action, SubmitAction):
                draft = action.draft
                if protected_insights:
                    repaired = [
                        row for row in draft.insights if row.insight_id not in protected_insights
                    ][: 5 - len(protected_insights)]
                    draft = draft.model_copy(
                        update={"insights": [*protected_insights.values(), *repaired]}
                    )
                report, compiler_errors = self.compile(company, filing, draft, observations)
                if compiler_errors and not repair_used and turns < MAX_TURNS:
                    passed_ids = {row.insight_id for row in report.insights}
                    protected_insights = {
                        row.insight_id: row
                        for row in draft.insights
                        if row.insight_id in passed_ids
                    }
                    repair_used = True
                    continue
                break
            if len(calls) >= MAX_TOOLS:
                terminal_reason = "tool_budget_exhausted"
                break
            key = f"{action.tool}:{_json_hash(action.arguments.model_dump(mode='json'))}"
            cached = key in cache
            rows = cache.get(key)
            if rows is None:
                rows = self._call_tool(company, filing, action)
                cache[key] = rows
            for row in rows:
                observations.setdefault(row.observation_id, row)
            calls.append(
                {
                    "tool": action.tool,
                    "arguments_sha256": _json_hash(action.arguments.model_dump(mode="json")),
                    "result_sha256": _json_hash([row.stable_hash for row in rows]),
                    "cached": cached,
                }
            )

        if draft is None:
            terminal_reason = (
                terminal_reason if terminal_reason != "submitted" else "turn_budget_exhausted"
            )
            draft = ResearchDraft(summary="Verified evidence was not sufficient for a deep report.")
        report, final_errors = self.compile(company, filing, draft, observations)
        dropped = {**compiler_errors, **final_errors}

        skeptic_drops: dict[str, list[str]] = {}
        if report.insights:
            decision = self._skeptic_review(report, observations)
            if decision is None:
                terminal_reason = "skeptic_unavailable"
            else:
                live_ids = {row.insight_id for row in report.insights}
                for objection in decision.objections:
                    if objection.insight_id in live_ids:
                        skeptic_drops.setdefault(objection.insight_id, []).append(objection.code)
                if skeptic_drops:
                    report = self._drop_insights(report, set(skeptic_drops))
                    dropped.update(skeptic_drops)
                    terminal_reason = "skeptic_pruned"

        # An explicitly unavailable obligation is a complete, honest answer. "Partial"
        # is reserved for protocol/budget degradation or insight-local pruning.
        partial = bool(dropped or terminal_reason != "submitted")
        trace = ResearchTrace(
            tool_calls=calls,
            obligation_transitions=report.obligations,
            tool_budget_used=len(calls),
            turn_budget_used=turns,
            repair_used=repair_used,
            dropped_insights={key: sorted(set(value)) for key, value in dropped.items()},
            model=getattr(self.generator, "model", "configured-model"),
            prompt_version=prompt_version,
            compiler_version=COMPILER_VERSION,
            terminal_reason=terminal_reason,
        )
        return HarnessResult(report, trace, "partial" if partial else "completed")

    def _turn_payload(
        self,
        company: Company,
        filing: Filing,
        observations: dict[str, ResearchObservation],
        compiler_errors: dict[str, list[str]],
        *,
        turns: int,
        calls: list[dict],
    ) -> str:
        section_keys = sorted(
            {
                row.section_key
                for row in self.service.repo.list_filing_sections(filing.accession_number)
            }
        )
        valuation = self.service.store.latest_valuation(company)
        peers = self.service.store.peers(
            company, self.service.store.profile(company, now=self.service.now_fn())
        )
        payload = {
            "ticker": company.ticker,
            "cik": company.cik,
            "filing": {
                "accession": filing.accession_number,
                "form": filing.form_type,
                "filed_at": filing.filed_at,
            },
            "open_obligations": self._open_obligations(observations, calls),
            "observations": [
                row.model_dump(mode="json")
                for row in sorted(observations.values(), key=lambda item: item.observation_id)
            ],
            "compiler_errors": compiler_errors,
            "remaining": {"turns": MAX_TURNS - turns, "tool_calls": MAX_TOOLS - len(calls)},
            "catalogs": {
                "sections": section_keys,
                "metrics": [row.value for row in MetricId],
                "risk_lenses": list(RiskLens.__args__),
                "valuation_available": bool(valuation and valuation.status == "computed"),
                "peers": [row.ticker for row in peers],
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _open_obligations(
        observations: dict[str, ResearchObservation], calls: list[dict]
    ) -> list[ResearchObligationId]:
        called = {row["tool"] for row in calls}
        required_tool = {
            "BUSINESS_ECONOMICS": "search_filing_sections",
            "IMPORTANT_CHANGES": "get_verified_changes",
            "FINANCIAL_QUALITY_AND_DOWNSIDE": "get_financial_context",
            "VALUATION_CONTEXT": "get_valuation_context",
            "PEER_CONTEXT": "get_peer_context",
        }
        open_rows = [
            obligation
            for obligation, tool in required_tool.items()
            if tool not in called
        ]
        rows = observations.values()
        filing_evidence = any(row.evidence_label == "fact" and row.evidence for row in rows)
        calculations = any(row.evidence_label == "calculation" for row in rows)
        if not (filing_evidence and calculations):
            open_rows.append("SOURCE_COVERAGE")
        return open_rows

    def _call_tool(self, company: Company, filing: Filing, action) -> list[ResearchObservation]:
        if isinstance(action, SearchAction):
            return self._search(filing, action.arguments)
        if isinstance(action, ChangesAction):
            return self._changes(filing)
        if isinstance(action, FinancialAction):
            return self._financial(company, action.arguments)
        if isinstance(action, ValuationAction):
            return self._valuation(company)
        return self._peers(company)

    def _observation(
        self,
        *,
        tool: str,
        label: str,
        text: str,
        evidence: list[EvidenceRef] | None = None,
        metric_ids: list[str] | None = None,
        as_of: str | None = None,
    ) -> ResearchObservation:
        digest = research_observation_hash(
            tool=tool,
            evidence_label=label,
            text=text,
            evidence=evidence or [],
            metric_ids=metric_ids or [],
            as_of=as_of,
        )
        return ResearchObservation(
            observation_id=f"o_{digest[:16]}",
            stable_hash=digest,
            tool=tool,
            evidence_label=label,
            text=text,
            evidence=evidence or [],
            metric_ids=metric_ids or [],
            as_of=as_of,
        )

    def _search(self, filing: Filing, args: SearchArgs) -> list[ResearchObservation]:
        sources: list[tuple[str, FilingSection]] = []
        if args.scope in {"current", "both"}:
            sources.extend(
                ("current", row)
                for row in self.service.repo.list_filing_sections(filing.accession_number)
            )
        if args.scope in {"prior", "both"}:
            prior = next(
                (
                    row
                    for row in self.service.repo.list_filings(filing.cik)
                    if row.filed_at < filing.filed_at
                    and base_form(row.form_type) == base_form(filing.form_type)
                    and not row.is_amendment
                ),
                None,
            )
            if prior:
                sources.extend(
                    ("prior", row)
                    for row in self.service.repo.list_filing_sections(prior.accession_number)
                )
        allowed = set(args.section_keys)
        rows: list[ResearchObservation] = []
        for scope, section in sources:
            if allowed and section.section_key not in allowed:
                continue
            lower = section.text.casefold()
            for query in args.queries:
                start = lower.find(query.strip().casefold())
                if start < 0:
                    continue
                left = max(0, section.text.rfind(". ", 0, start) + 2)
                right = section.text.find(". ", start + len(query))
                right = min(len(section.text), right + 1 if right >= 0 else start + 500)
                if right - left > 700:
                    left, right = max(0, start - 180), min(len(section.text), start + 500)
                quote = section.text[left:right]
                evidence = EvidenceRef(
                    kind="filing",
                    reference_id=f"section:{section.accession_number}:{section.section_key}:{left}",
                    accession=section.accession_number,
                    section_key=section.section_key,
                    char_start=left,
                    char_end=right,
                    quote=quote,
                    section_sha256=section.text_sha256,
                )
                rows.append(
                    self._observation(
                        tool="search_filing_sections",
                        label="fact",
                        text=f"{scope.title()} filing evidence: {quote}",
                        evidence=[evidence],
                        as_of=filing.filed_at[:10],
                    )
                )
                if len(rows) >= 6:
                    return rows
        return rows

    def _changes(self, filing: Filing) -> list[ResearchObservation]:
        detail = self.service.presentation.filing(filing.accession_number)
        if detail is None:
            return []
        rows = []
        for finding in detail.filing.findings:
            evidence = [
                EvidenceRef(
                    kind="filing",
                    reference_id=row.claim_id,
                    accession=row.accession,
                    section_key=row.section_key,
                    char_start=row.char_start,
                    char_end=row.char_end,
                    quote=row.quote,
                    section_sha256=row.section_sha256,
                )
                for row in finding.evidence
            ]
            rows.append(
                self._observation(
                    tool="get_verified_changes",
                    label="fact",
                    text=finding.headline,
                    evidence=evidence,
                    metric_ids=[finding.metric_id] if finding.metric_id else [],
                    as_of=filing.filed_at[:10],
                )
            )
        return rows

    def _financial(self, company: Company, args: FinancialArgs) -> list[ResearchObservation]:
        rows: list[ResearchObservation] = []
        metric_view = self.service.presentation.metrics(
            company.ticker, as_of=date.today().isoformat()
        )
        selected = {item.value for item in args.metric_ids}
        by_metric = {row.metric: row for row in metric_view.rows} if metric_view else {}
        for metric_id in selected:
            row = by_metric.get(metric_id)
            if row is None or row.state != "computed":
                rows.append(
                    self._observation(
                        tool="get_financial_context",
                        label="unavailable",
                        text=f"{metric_id.replace('_', ' ')} is unavailable from verified metrics.",
                        metric_ids=[metric_id],
                        as_of=metric_view.as_of if metric_view else None,
                    )
                )
                continue
            rows.append(
                self._observation(
                    tool="get_financial_context",
                    label="calculation",
                    text=f"{metric_id.replace('_', ' ').title()}: {row.value}.",
                    evidence=[
                        EvidenceRef(
                            kind="metric",
                            reference_id=f"computation:{row.source_computation_id}:{metric_id}",
                        )
                    ],
                    metric_ids=[metric_id],
                    as_of=row.effective_as_of,
                )
            )
        risks = {row.lens: row for row in self.service.risk_radar(company.ticker) or []}
        for lens in args.risk_lenses:
            risk = risks.get(lens)
            if risk is None:
                continue
            rows.append(
                self._observation(
                    tool="get_financial_context",
                    label="unavailable" if risk.status == "unavailable" else "calculation",
                    text=f"{lens.replace('_', ' ').title()} is {risk.status}: {risk.explanation}",
                    evidence=risk.evidence,
                    metric_ids=risk.metric_ids,
                    as_of=risk.freshness,
                )
            )
        latest = self.service._latest_supported(company)
        if latest is not None:
            projection = load_filing_projection(self.service.repo, latest)
            for check_id, detail in projection.data_quality:
                analysis_id = projection.p1_analysis.id if projection.p1_analysis else None
                rows.append(
                    self._observation(
                        tool="get_financial_context",
                        label="calculation",
                        text=f"Accounting check {check_id} requires review: {detail}",
                        evidence=(
                            [
                                EvidenceRef(
                                    kind="metric",
                                    reference_id=f"verification:{analysis_id}:{check_id}",
                                )
                            ]
                            if analysis_id is not None
                            else []
                        ),
                        as_of=latest.filed_at[:10],
                    )
                )
        return rows[:12]

    def _valuation(self, company: Company) -> list[ResearchObservation]:
        run = self.service.store.latest_valuation(company)
        if run is None or run.status != "computed":
            return [
                self._observation(
                    tool="get_valuation_context",
                    label="unavailable",
                    text=(
                        "Valuation context is unavailable until the user enters "
                        "a current price and date."
                    ),
                )
            ]
        parts = [f"saved price {run.price:g}", run.label.lower()]
        if run.trailing_pe is not None:
            parts.append(f"trailing P/E {run.trailing_pe:.1f}")
        if run.price_to_fcf is not None:
            parts.append(f"price to FCF {run.price_to_fcf:.1f}")
        if run.fcf_yield is not None:
            parts.append(f"FCF yield {run.fcf_yield:.1%}")
        if run.reverse_dcf_growth is not None:
            parts.append(f"reverse-DCF growth {run.reverse_dcf_growth:.1%}")
        parts.extend(
            f"{row.name} scenario value {row.implied_value_per_share:g}"
            + (
                f" ({row.change_percent:+.1f}% versus the saved price)"
                if row.change_percent is not None
                else ""
            )
            for row in run.scenarios
        )
        return [
            self._observation(
                tool="get_valuation_context",
                label="calculation",
                text="Saved valuation context: " + "; ".join(parts) + ".",
                evidence=[
                    EvidenceRef(kind="valuation", reference_id=f"valuation:{run.run_id}"),
                    *run.inputs[:5],
                ],
                metric_ids=[],
                as_of=run.price_as_of,
            )
        ]

    def _peers(self, company: Company) -> list[ResearchObservation]:
        peers = self.service.store.peers(
            company, self.service.store.profile(company, now=self.service.now_fn())
        )
        rows = []
        for peer in peers[:3]:
            metrics = self.service.repo.latest_computations(peer.ticker)
            computed = [row for row in metrics if row.status == "computed" and row.id is not None][
                :4
            ]
            risks = [
                row
                for row in self.service.risk_radar(peer.ticker) or []
                if row.status != "unavailable"
                and row.evidence
                and all(ref.kind == "metric" for ref in row.evidence)
            ][:3]
            if not computed and not risks:
                rows.append(
                    self._observation(
                        tool="get_peer_context",
                        label="unavailable",
                        text=(
                            f"{peer.ticker} is a possible comparison, but verified "
                            "peer metrics are unavailable."
                        ),
                    )
                )
                continue
            metric_names = list(
                dict.fromkeys(
                    [row.tool for row in computed]
                    + [metric_id for risk in risks for metric_id in risk.metric_ids]
                )
            )
            risk_text = (
                "; risk context "
                + ", ".join(f"{risk.lens.replace('_', ' ')} {risk.status}" for risk in risks)
                if risks
                else ""
            )
            evidence = [
                EvidenceRef(kind="metric", reference_id=f"computation:{row.id}:{row.tool}")
                for row in computed
            ]
            evidence.extend(ref for risk in risks for ref in risk.evidence)
            evidence = list({ref.reference_id: ref for ref in evidence}.values())[:6]
            as_of_values = [row.as_of for row in computed] + [
                risk.freshness for risk in risks if risk.freshness
            ]
            rows.append(
                self._observation(
                    tool="get_peer_context",
                    label="calculation",
                    text=(
                        f"{peer.ticker} is a possible SEC-industry comparison with "
                        f"verified {', '.join(row.tool for row in computed) or 'risk'} context"
                        f"{risk_text}."
                    ),
                    evidence=evidence,
                    metric_ids=metric_names,
                    as_of=max(as_of_values) if as_of_values else None,
                )
            )
        return rows or [
            self._observation(
                tool="get_peer_context",
                label="unavailable",
                text="No already-ingested peer context is available.",
            )
        ]

    def compile(
        self,
        company: Company,
        filing: Filing,
        draft: ResearchDraft,
        observations: dict[str, ResearchObservation],
    ) -> tuple[CompanyResearchReport, dict[str, list[str]]]:
        errors: dict[str, list[str]] = {}
        duplicate_ids = {
            row.insight_id
            for row in draft.insights
            if sum(item.insight_id == row.insight_id for item in draft.insights) > 1
        }
        accepted: list[ResearchInsight] = []
        allowed_peer_tickers = {
            row.ticker
            for row in self.service.store.peers(
                company, self.service.store.profile(company, now=self.service.now_fn())
            )
        }
        for item in draft.insights:
            codes: list[str] = []
            if item.insight_id in duplicate_ids:
                codes.append("DUPLICATE_INSIGHT_ID")
            authored = [
                item.headline,
                item.evidence_summary,
                item.driver,
                item.implication,
                *item.assumptions,
                *item.limitations,
            ]
            if any(authored_text_violations(text) for text in authored):
                codes.append("UNSAFE_AUTHORED_TEXT")
            refs = [observations.get(obs_id) for obs_id in item.observation_ids]
            if any(row is None for row in refs):
                codes.append("UNKNOWN_OBSERVATION")
            valid_refs = [row for row in refs if row is not None]
            for row in valid_refs:
                if not self._valid_observation(company, filing, row, allowed_peer_tickers):
                    codes.append("INVALID_PROVENANCE")
            if valid_refs and all(row.evidence_label == "unavailable" for row in valid_refs):
                codes.append("INSUFFICIENT_EVIDENCE")
            expected_tool = {
                "business": {"search_filing_sections"},
                "change": {"get_verified_changes", "search_filing_sections"},
                "financial_quality": {"get_financial_context"},
                "valuation": {"get_valuation_context"},
                "peer": {"get_peer_context"},
            }[item.category]
            if not any(
                row.tool in expected_tool and row.evidence_label != "unavailable"
                for row in valid_refs
            ):
                codes.append("CATEGORY_EVIDENCE_MISSING")
            if item.category == "valuation" and not any(
                row.tool == "get_valuation_context" and row.evidence_label == "calculation"
                for row in valid_refs
            ):
                codes.append("VALUATION_UNAVAILABLE")
            if codes:
                errors[item.insight_id] = sorted(set(codes))
                continue
            accepted.append(
                ResearchInsight(
                    **item.model_dump(),
                    evidence_status="conditional_inference",
                )
            )
        obligations = self._obligations(accepted, observations)
        summary = draft.summary
        if authored_text_violations(summary):
            summary = (
                "RipplX connected the available filing evidence, verified calculations, "
                "saved valuation context, and comparable-company context."
            )
        if not accepted:
            summary = "No qualitative insight passed the deterministic research compiler."
        gaps = [self._gap(row.obligation) for row in obligations if row.state == "unavailable"]
        valuation_context = next(
            (
                row
                for row in observations.values()
                if row.tool == "get_valuation_context"
            ),
            None,
        )
        report = CompanyResearchReport(
            ticker=company.ticker,
            cik=company.cik,
            as_of=filing.filed_at[:10],
            data_cutoff=self.service.now_fn()[:10],
            summary=summary,
            obligations=obligations,
            insights=accepted,
            observations=sorted(observations.values(), key=lambda row: row.observation_id)[:24],
            valuation_context=valuation_context,
            evidence_gaps=gaps,
            disclaimer=DISCLAIMER,
        )
        return report, errors

    def _valid_observation(
        self,
        company: Company,
        filing: Filing,
        observation: ResearchObservation,
        allowed_peer_tickers: set[str],
    ) -> bool:
        if observation.observation_id != f"o_{observation.stable_hash[:16]}":
            return False
        allowed_accessions = {filing.accession_number}
        allowed_accessions.update(
            row.accession_number
            for row in self.service.repo.list_filings(company.cik)
            if row.filed_at < filing.filed_at
            and base_form(row.form_type) == base_form(filing.form_type)
        )
        for ref in observation.evidence:
            if ref.kind == "filing":
                if ref.accession not in allowed_accessions or ref.section_key is None:
                    return False
                section = next(
                    (
                        row
                        for row in self.service.repo.list_filing_sections(ref.accession)
                        if row.section_key == ref.section_key
                    ),
                    None,
                )
                if section is None or ref.section_sha256 != section.text_sha256:
                    return False
                if None in {ref.char_start, ref.char_end, ref.quote}:
                    return False
                if section.text[ref.char_start : ref.char_end] != ref.quote:
                    return False
            elif ref.kind == "metric":
                parts = ref.reference_id.split(":")
                if len(parts) != 3 or not parts[1].isdigit():
                    return False
                if parts[0] == "computation":
                    row = self.service.repo.conn.execute(
                        "SELECT ticker, tool FROM computations WHERE id = ?", (int(parts[1]),)
                    ).fetchone()
                    if (
                        row is None
                        or row["tool"] != parts[2]
                        or (
                            row["ticker"] != company.ticker
                            and row["ticker"] not in allowed_peer_tickers
                        )
                    ):
                        return False
                elif parts[0] == "verification":
                    row = self.service.repo.conn.execute(
                        """SELECT a.accession_number, v.check_id
                             FROM verification_results v
                             JOIN analyses a ON a.id = v.analysis_id
                            WHERE v.analysis_id = ? AND v.check_id = ? AND v.verdict = 'warn'""",
                        (int(parts[1]), parts[2]),
                    ).fetchone()
                    if row is None or row["accession_number"] not in allowed_accessions:
                        return False
                else:
                    return False
            elif ref.kind == "valuation":
                run_id = ref.reference_id.removeprefix("valuation:")
                row = self.service.repo.conn.execute(
                    "SELECT 1 FROM valuation_runs WHERE id = ? AND user_id = ? AND cik = ?",
                    (run_id, self.service.user_id, company.cik),
                ).fetchone()
                if row is None:
                    return False
        return True

    @staticmethod
    def _obligations(
        insights: list[ResearchInsight], observations: dict[str, ResearchObservation]
    ) -> list[ResearchObligation]:
        categories = {row.category for row in insights}
        rows = list(observations.values())
        mapping = {
            "BUSINESS_ECONOMICS": ("business", "search_filing_sections"),
            "IMPORTANT_CHANGES": ("change", "get_verified_changes"),
            "FINANCIAL_QUALITY_AND_DOWNSIDE": ("financial_quality", "get_financial_context"),
            "VALUATION_CONTEXT": ("valuation", "get_valuation_context"),
            "PEER_CONTEXT": ("peer", "get_peer_context"),
        }
        result = []
        for obligation in OBLIGATIONS[:-1]:
            category, tool = mapping[obligation]
            state = (
                "supported"
                if category in categories
                else "mixed"
                if any(row.tool == tool and row.evidence_label != "unavailable" for row in rows)
                else "unavailable"
            )
            result.append(ResearchObligation(obligation=obligation, state=state))
        filing_evidence = any(row.evidence_label == "fact" and row.evidence for row in rows)
        calculations = any(row.evidence_label == "calculation" for row in rows)
        result.append(
            ResearchObligation(
                obligation="SOURCE_COVERAGE",
                state="supported"
                if filing_evidence and calculations
                else "mixed"
                if filing_evidence or calculations
                else "unavailable",
            )
        )
        return result

    @staticmethod
    def _gap(obligation: ResearchObligationId) -> str:
        return {
            "BUSINESS_ECONOMICS": "Business-model evidence was not established.",
            "IMPORTANT_CHANGES": "No verified filing change was available.",
            "FINANCIAL_QUALITY_AND_DOWNSIDE": "Verified financial context was unavailable.",
            "VALUATION_CONTEXT": "Enter a current price and date to add valuation context.",
            "PEER_CONTEXT": "No already-ingested peer comparison was available.",
            "SOURCE_COVERAGE": "The available SEC and calculation sources were incomplete.",
        }[obligation]

    def _skeptic_review(
        self,
        report: CompanyResearchReport,
        observations: dict[str, ResearchObservation],
    ) -> SkepticDecision | None:
        system, _ = load_prompt(STAGE_COMPANY_RESEARCH_SKEPTIC)
        user = json.dumps(
            {
                "report": report.model_dump(mode="json"),
                "observations": [
                    row.model_dump(mode="json")
                    for row in sorted(observations.values(), key=lambda item: item.observation_id)
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            return SkepticDecision.model_validate(
                extract_json(self.skeptic.complete(system=system, user=user).text)
            )
        except Exception:  # noqa: BLE001 - optional review degrades to compiler baseline
            return None

    def _drop_insights(
        self, report: CompanyResearchReport, drop_ids: set[str]
    ) -> CompanyResearchReport:
        surviving = [row for row in report.insights if row.insight_id not in drop_ids]
        return report.model_copy(
            update={
                "insights": surviving,
                "obligations": self._obligations(
                    surviving, {row.observation_id: row for row in report.observations}
                ),
                "summary": report.summary
                if surviving
                else "No qualitative insight passed the deterministic research compiler.",
            }
        )
