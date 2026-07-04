"""Adapters: P1/P2 outputs + holding + metrics → matrix.evaluate() inputs.

Thin and dumb (SYSTEM_DESIGN §4.4): they carry values across, adding no logic of their
own. Their one real job is mapping P1's red-flag vocabulary to the exact
CRITICAL_DOC_FLAGS codes the matrix keys on (M1 fires on that intersection).
"""
from __future__ import annotations

import re

from finwatch.core.types import CRITICAL_DOC_FLAGS
from finwatch.db.repositories import Holding
from finwatch.llm.schemas import P1Output, P2Output
from finwatch.metrics.envelope import MetricsBundle
from finwatch.signals.matrix import ExtractionSummary, ImpactSummary, Record

# P1's RedFlag.flag is FREE TEXT and RedFlag.severity is P1's materiality judgment. The matrix
# keys M1 (STRONG_REVIEW_SELL, zero metrics) on the EXACT CRITICAL_DOC_FLAGS codes and consumes
# NO severity, so this adapter is the sole bridge from P1's vocabulary to those codes. It must,
# at once, satisfy two opposing acceptance gates:
#   • gate #2 (100% critical recall): recognize a critical CONCEPT across the many descriptive
#     phrasings a live model emits — "Substantial doubt about the Company's ability to continue
#     as a going concern", "Material weaknesses in internal control over financial reporting",
#     "Resignation of the independent registered public accounting firm", "Acceleration of
#     indebtedness". So we match on CONTAINED keywords, not exact tokens.
#   • gate #4 (boring-filing silence): the SAME lexicon words appear as routine risk-factor /
#     notes boilerplate ("subject to unauthorized access, ransomware", "if we fail to satisfy
#     continued-listing standards", "restated to conform to current presentation"). Keyword
#     matching alone would fire M1 on every such filing. So we gate on P1's own severity: P1's
#     hard-floor table rates a GENUINE occurrence of each concept ≥ HIGH (Item 1.03/3.01/2.04/
#     4.02 are CRITICAL; going-concern, material weakness, auditor resignation never below
#     HIGH), while boilerplate/hypothetical mentions are rated lower. We defer the materiality
#     call to P1, where it belongs, rather than re-deciding it here.
# The Item 1.05 cyber CRITICAL tier is stricter still: base 1.05 is HIGH and only a MATERIAL
# incident is CRITICAL, so a cyber concept maps to the critical-tier code only at 'critical'.

_CANON_RE = re.compile(r"[^a-z0-9]+")
_HARD_FLOOR_SEVERITIES = frozenset({"critical", "high"})
_CYBER_CRITICAL_TIER = "cyber_1_05_critical_tier"


def _canonical(flag: str) -> str:
    """Lowercase and collapse every non-alphanumeric run to a single "_"
    ("Non-Reliance" → "non_reliance", "going concern" → "going_concern")."""
    return _CANON_RE.sub("_", flag.strip().lower()).strip("_")


def _concept_code(f: str) -> str | None:
    """The critical CONCEPT a canonical flag denotes (before the severity gate), or None.

    Substring containment so descriptive phrasings match — real filings say "Acceleration of
    the 5.5% Senior Notes", "Nasdaq Listing Rule 5550 (minimum bid price)", "should no longer
    be relied upon", not the bare token. Keyword lists are deliberately liberal because the
    severity gate (see critical_code) — not the keywords — is what suppresses benign/boilerplate
    mentions. Two concepts still carry AND-guards where a bare keyword is genuinely ambiguous:
    acceleration must co-occur with a debt instrument/term (so "revenue acceleration" is out),
    and a resignation must name the auditor (so an officer's "cfo resigned" — Item 5.02 — is out).
    """
    def has(*subs: str) -> bool:
        return any(s in f for s in subs)

    if has("going_concern", "substantial_doubt"):
        return "going_concern"
    if has("material_weakness"):                        # also matches "material_weaknesses"
        return "material_weakness_with_restatement_risk"
    if has("non_reliance", "no_longer_be_relied", "restate"):   # 4.02 disclosure language
        return "item_4_02_non_reliance"
    if has("bankruptcy", "chapter_11", "chapter_7", "receivership"):
        return "item_1_03_bankruptcy"
    if has("delist", "listing_standard", "listing_rule", "listing_requirement",
           "listing_qualification", "continued_listing", "minimum_bid", "bid_price"):
        return "item_3_01_delisting"
    if has("resign") and has("auditor", "accounting_firm", "accountant"):
        return "auditor_resignation"
    if has("triggering_event", "debt_acceleration", "event_of_default") or (
        has("accelerat") and has("debt", "indebted", "obligation", "covenant", "loan", "credit",
                                  "default", "senior_note", "debenture", "indenture", "bond",
                                  "borrowing", "notes_payable")):
        return "item_2_04_acceleration"
    if has("ransomware", "cyberattack", "cyber_attack", "unauthorized_access", "data_breach",
           "security_breach", "cyber_incident", "cybersecurity"):
        return _CYBER_CRITICAL_TIER
    return None


def critical_code(flag: str, severity: str | None = None) -> str | None:
    """The matrix CRITICAL_DOC_FLAGS code a P1 red-flag (label + P1's severity) confirms, else None.

    A flag denotes a critical concept if it either equals a canonical code or matches a concept
    keyword. Either way it is CONFIRMED critical only when P1 rated it at the severity the concept
    warrants — ≥ HIGH for the hard-floor concepts, exactly 'critical' for the Item 1.05 cyber tier.
    The severity gate applies even to a verbatim canonical code, because a couple of codes collide
    with natural language: negative-assurance boilerplate labeled "going concern" (management found
    NO doubt) canonicalizes to the going_concern code, and honoring that verbatim would fire M1 on
    a healthy filing. Deferring the materiality call to P1's severity keeps M1 silent there. Returns
    None for anything not confirmed-critical, so red_flag_codes carries only real critical codes.
    """
    f = _canonical(flag)
    code = f if f in CRITICAL_DOC_FLAGS else _concept_code(f)
    if code is None:
        return None
    sev = (severity or "").strip().lower()
    gate = {"critical"} if code == _CYBER_CRITICAL_TIER else _HARD_FLOOR_SEVERITIES
    return code if sev in gate else None


def to_extraction_summary(p1: P1Output) -> ExtractionSummary:
    return ExtractionSummary(
        red_flag_codes=[c for rf in p1.red_flags
                        if (c := critical_code(rf.flag, rf.severity)) is not None],
        has_red_flags=bool(p1.red_flags),   # ANY flag blocks M7 (critical ones fire M1)
        extraction_confidence=p1.extraction_confidence,
        gaps=list(p1.gaps),
    )


def to_impact_summary(p2: P2Output | None, ticker: str) -> ImpactSummary:
    if p2 is None:
        return ImpactSummary()  # defaults: not_assessable / unclear / none_stated
    rec = next((r for r in p2.records_affected if r.ticker.upper() == ticker.upper()), None)
    if rec is None:
        return ImpactSummary()
    return ImpactSummary(
        thesis_verdict=rec.thesis_check.verdict,
        net_direction=rec.net_direction,
        guidance_direction=rec.guidance_direction,
    )


def to_record(holding: Holding, metrics: MetricsBundle) -> Record:
    pm = metrics.get("position_metrics")
    comps = pm.components if (pm and pm.computed) else {}
    return Record(
        ticker=holding.ticker,
        owned=bool(holding.owned),
        current_weight_pct=comps.get("weight_pct"),
        target_weight_pct=holding.target_weight_pct,
        unrealized_pl_pct=comps.get("unrealized_pl_pct"),
        thesis=holding.thesis,
    )
