"""Deterministic verifier — the compile pass. Trust-critical (test-guarded): edit with care, keep the spec tests green.

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
# A label immediately before the number that marks it as a reference, not a
# financial quantity: "Item 2.02", "Rule 10b5-1", "§ 5", "c_0001", "CIK 320193".
# Matched against the UNSTRIPPED left-context so the mandatory-\s patterns
# (Item\s / Rule\s / phase\s) actually see the space that precedes the number.
# (The old M(?=\d$)/V(?=\d$)/F/v branches were inert — the digit they look ahead
# for lives past the end of `before`, never inside it — so they are dropped.)
_WHITELIST_BEFORE = re.compile(
    r"(Item\s|Rule\s|§\s?|c_|claim_|accession|CIK\s?|phase\s)", re.IGNORECASE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Written-date parts: a month name immediately before the day, or ', YYYY' right
# after it — used (with a 1–31 day bound) to whitelist calendar dates.
_MONTH_BEFORE = re.compile(
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\.?\s+$", re.IGNORECASE)
_DAY_YEAR_AFTER = re.compile(r"\s*,\s*(?:19|20)\d{2}\b")


class NumberToken(BaseModel):
    raw: str
    value: float
    tolerance: float
    position: int


def _is_date_part(text: str, m: "re.Match[str]") -> bool:
    """True when the matched number is a calendar-date component that needs no
    financial provenance: any field of an ISO date (YYYY-MM-DD), or a day-of-month
    (1–31) in a written date ('March 15, 2024' / 'March 15' / '15, 2024').

    Kept deliberately tight so it never launders a real financial figure: the day is
    bounded 1–31 and must sit directly beside a month name or a ', YYYY'."""
    s, e = m.start(), m.end()
    # ISO date: the token's span lies fully inside a YYYY-MM-DD occurrence. The
    # window is wide enough (±10) that the day component still sees the leading year.
    ws = max(0, s - 10)
    window = text[ws:e + 10]
    for dm in _ISO_DATE.finditer(window):
        if ws + dm.start() <= s and e <= ws + dm.end():
            return True
    # Written date: a bare 1- or 2-digit day (1–31) next to a month or ', YYYY'.
    num = m.group("num")
    is_day = (m.group("dec") is None and m.group("cur") is None
              and m.group("suf") is None and num.isdigit()
              and 1 <= int(num) <= 31)
    if is_day and (_MONTH_BEFORE.search(text[max(0, s - 12):s])
                   or _DAY_YEAR_AFTER.match(text[e:e + 8])):
        return True
    return False


def extract_number_tokens(text: str) -> list[NumberToken]:
    tokens: list[NumberToken] = []
    for m in _NUM.finditer(text):
        s, e = m.start(), m.end()
        raw = text[s:e]
        # whitelists ------------------------------------------------------
        if _is_date_part(text, m):
            continue                                    # ISO / written calendar dates
        if _WHITELIST_AFTER.match(text[e:e + 4]):
            continue                                    # form names 10-K etc.
        before = text[max(0, s - 12):s]
        if _WHITELIST_BEFORE.search(before):            # UNSTRIPPED: keeps the 'Item ' space
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
