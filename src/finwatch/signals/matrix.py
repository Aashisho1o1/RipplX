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
