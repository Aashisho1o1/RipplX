"""P3 signal engine: the matrix decides, the LLM writes rationale, the shadow log records.

The matrix (signals/matrix.py, Tier 1) is AUTHORITATIVE — it sets the posture and the
hypothetical signal. The LLM only writes prose and may REQUEST a one-notch escalation
TOWARD CAUTION; the engine applies + logs valid requests and ignores anything toward
aggression. Runs ONLY for owned records. Every evaluation is written to
signal_shadow_log unconditionally (shadow mode); the default digest ships postures only.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from finwatch.core.types import DISCLAIMER
from finwatch.db.repositories import Analysis, Repo, SignalShadowLog
from finwatch.llm.prompts import STAGE_P3, load_prompt
from finwatch.llm.router import LLMClient, extract_json
from finwatch.llm.schemas import EscalationRequest, P3Output, RuleSkipped
from finwatch.metrics.envelope import MetricsBundle
from finwatch.metrics.formulas import PriceProvider
from finwatch.signals.matrix import (
    Decision,
    ExtractionSummary,
    ImpactSummary,
    Record,
    apply_escalation,
    evaluate,
)

# Metrics the matrix rules consult — snapshotted verbatim for P3 + the shadow log.
_MATRIX_METRICS = ("altman_z", "piotroski_f", "simple_leverage", "liquidity_basics",
                   "rebalance_check", "position_metrics")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def computed_inputs_snapshot(metrics: MetricsBundle) -> list[dict]:
    """Verbatim metric results the matrix consulted (P3 quotes these; shadow log stores them)."""
    out: list[dict] = []
    for name in _MATRIX_METRICS:
        r = metrics.get(name)
        if r is not None:
            out.append(json.loads(r.model_dump_json()))
    out.extend(json.loads(v.model_dump_json()) for v in metrics.valuations)
    return out


@dataclass
class SignalResult:
    decision: Decision
    p3: P3Output | None = None
    analysis_id: int | None = None
    shadow_log_id: int | None = None
    escalated: bool = False


def render_shadow_report(rows: list[SignalShadowLog]) -> str:
    """Summarise the shadow-signal track record (`finwatch shadow report`)."""
    if not rows:
        return "No shadow-signal evaluations logged yet.\n"
    from collections import Counter

    postures = Counter(r.review_posture for r in rows)
    signals = Counter(r.hypothetical_signal for r in rows)
    reviewed = sum(1 for r in rows if r.outcome_reviewed_at)
    lines = [
        f"# Shadow-signal track record ({len(rows)} evaluations)",
        "",
        "Review postures: " + ", ".join(f"{k}={v}" for k, v in postures.most_common()),
        "Hypothetical signals: " + ", ".join(f"{k}={v}" for k, v in signals.most_common()),
        f"Outcomes reviewed: {reviewed}/{len(rows)}",
        "",
        "Signals are UNVALIDATED, educational shadow output. Promotion to default-visible "
        "requires ≥100 logged evaluations, a human audit of ≥20 sampled cases, and "
        "passing the acceptance gates (see README).",
    ]
    return "\n".join(lines) + "\n"


class SignalEngine:
    def __init__(
        self,
        repo: Repo,
        llm: LLMClient,
        *,
        price_provider: PriceProvider | None = None,
        model_label: str | None = None,
        now_fn: Callable[[], str] = _now_iso,
    ) -> None:
        self.repo = repo
        self.llm = llm
        self.price_provider = price_provider
        self.model_label = model_label
        self._now_fn = now_fn

    def run(
        self,
        *,
        record: Record,
        extraction: ExtractionSummary,
        impact: ImpactSummary,
        metrics: MetricsBundle,
        accession_number: str,
        ticker: str,
        as_of: str,
        disclaimer: str = DISCLAIMER,
    ) -> SignalResult:
        decision = evaluate(record, extraction, impact, metrics)
        # P3 is owned-only; watch records get NOT_APPLICABLE_WATCHLIST and no rationale/log.
        if not record.owned or decision.signal == "NOT_APPLICABLE_WATCHLIST":
            return SignalResult(decision=decision)

        computed = computed_inputs_snapshot(metrics)
        p3_out, decision, aid = self._rationale(
            decision, extraction, impact, record, computed, accession_number, ticker, disclaimer)
        shadow_id = self._shadow_log(decision, computed, accession_number, ticker, as_of)
        return SignalResult(
            decision=decision, p3=p3_out, analysis_id=aid, shadow_log_id=shadow_id,
            escalated=decision.escalation is not None,
        )

    def _rationale(
        self, decision: Decision, extraction: ExtractionSummary, impact: ImpactSummary,
        record: Record, computed: list[dict], accession: str, ticker: str, disclaimer: str,
    ) -> tuple[P3Output, Decision, int]:
        system, version = load_prompt(STAGE_P3)
        inputs = {
            "decision": {
                "posture": decision.posture, "hypothetical_signal": decision.signal,
                "rules_fired": decision.rules_fired, "rules_skipped": decision.rules_skipped,
                "computed_inputs": computed, "caps": decision.caps_applied,
            },
            "extraction": extraction.model_dump(),
            "impact": impact.model_dump(),
            "record": record.model_dump(),
        }
        resp = self.llm.complete(
            system=system, user=json.dumps(inputs, ensure_ascii=False, default=str),
            temperature=0.2, json_mode=True)
        llm_out = P3Output.model_validate(extract_json(resp.text))  # prose fields only

        # Apply only a VALID one-notch-toward-caution escalation; ignore aggression.
        if llm_out.escalation_request is not None:
            req = llm_out.escalation_request
            try:
                decision = apply_escalation(decision, req.to, req.justification)
            except ValueError:
                pass

        # The final P3 output is the ENGINE decision + the LLM's prose (never the LLM's
        # echoed posture/signal), so V3's re-derivation always matches.
        final = P3Output(
            ticker=ticker, accession_number=accession,
            review_posture=decision.posture or "insufficient_data",
            trade_action=None,
            hypothetical_signal=decision.signal,
            rules_fired=list(decision.rules_fired),
            rules_skipped=[RuleSkipped(rule=s["rule"], reason=s["reason"])
                           for s in decision.rules_skipped],
            computed_inputs=computed,
            rationale=llm_out.rationale, counter_evidence=llm_out.counter_evidence,
            what_would_change_this=list(llm_out.what_would_change_this),
            escalation_request=(
                EscalationRequest(to=decision.escalation["to"],
                                  justification=decision.escalation["justification"])
                if decision.escalation else None),
            confidence=llm_out.confidence, disclaimer=disclaimer,
        )
        aid = self.repo.insert_analysis(Analysis(
            accession_number=accession, ticker=ticker, stage="P3",
            model=self.model_label or resp.model, prompt_version=version,
            output_json=final.model_dump_json(), tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out, cost_usd=resp.cost_usd, created_at=self._now_fn()))
        return final, decision, aid

    def _shadow_log(
        self, decision: Decision, computed: list[dict], accession: str, ticker: str, as_of: str,
    ) -> int:
        price = (self.price_provider.close_on_or_before(ticker, as_of)
                 if self.price_provider else None)
        return self.repo.insert_shadow_log(SignalShadowLog(
            accession_number=accession, ticker=ticker,
            review_posture=decision.posture or "insufficient_data",
            hypothetical_signal=decision.signal,
            rules_fired_json=json.dumps(decision.rules_fired),
            rules_skipped_json=json.dumps(decision.rules_skipped),
            computed_inputs_json=json.dumps(computed),
            price_at_eval=price, created_at=self._now_fn()))
