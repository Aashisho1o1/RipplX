"""Verifier orchestration: §14 regeneration policy, persistence, bundle helpers.

(The verifier checks themselves are covered by the Tier 1 mutation battery.)
"""
from __future__ import annotations

import hashlib

from finwatch.core.types import DISCLAIMER, SectorClass, SectorInfo
from finwatch.db import Filing, FilingSection, Repo, XbrlFact, init_db
from finwatch.metrics.envelope import MetricsBundle
from finwatch.signals.matrix import ExtractionSummary, ImpactSummary, Record, evaluate
from finwatch.verify import (
    fact_values_from_repo,
    persist_report,
    run_all,
    run_with_regeneration,
    section_texts_from_repo,
)
from finwatch.verify.checks import EvidenceClaim, VerifyBundle
from finwatch.xbrl.normalize import Fact, FactStore


def _bundle(rendered: str, *, evidence=None, section_texts=None) -> VerifyBundle:
    """A valid bundle whose only variable is the rendered text (and optional claims)."""
    metrics = MetricsBundle()
    record = Record(ticker="T", owned=True, current_weight_pct=5.0,
                    target_weight_pct=10.0, thesis="t")
    ext = ExtractionSummary()
    imp = ImpactSummary(thesis_verdict="intact", net_direction="neutral")
    decision = evaluate(record, ext, imp, metrics)
    return VerifyBundle(
        rendered_text=rendered, metrics=metrics, fact_store_values=[],
        evidence_claims=evidence or [], section_texts=section_texts or {},
        decision=decision, record=record, extraction=ext, impact=imp,
        trade_action=None, disclaimer_text=DISCLAIMER,
    )


CLEAN = "This looks likely to continue improving next period."
FORBIDDEN = "This outcome is guaranteed to continue improving."  # 'guaranteed' -> V5 blocking fail

GENERAL = SectorInfo(SectorClass.GENERAL, False)


def _store(assets: float, liabilities: float, equity: float) -> FactStore:
    def inst(tag, val):
        return Fact(taxonomy="us-gaap", tag=tag, unit="USD", value=val,
                    end="2024-01-01", filed="2024-05-01", accn="a")

    return FactStore([inst("Assets", assets), inst("Liabilities", liabilities),
                      inst("StockholdersEquity", equity)])


# ---- sanity: the two archetype bundles behave as designed ------------------
def test_clean_bundle_passes_and_forbidden_fails():
    assert run_all(_bundle(CLEAN)).verdict in ("PASS", "PASS_WITH_WARNINGS")
    assert run_all(_bundle(FORBIDDEN)).verdict == "FAIL"


# ---- regeneration policy ---------------------------------------------------
def test_pass_needs_no_regeneration():
    outcome = run_with_regeneration(_bundle(CLEAN), lambda _r, _n: None)
    assert outcome.regenerations == 0
    assert not outcome.manual_review


def test_regeneration_fixes_on_first_retry():
    calls = []

    def regen(_report, n):
        calls.append(n)
        return _bundle(CLEAN)

    outcome = run_with_regeneration(_bundle(FORBIDDEN), regen)
    assert outcome.regenerations == 1
    assert not outcome.manual_review
    assert calls == [1]


def test_regeneration_fixes_on_second_retry():
    def regen(_report, n):
        return _bundle(FORBIDDEN) if n == 1 else _bundle(CLEAN)

    outcome = run_with_regeneration(_bundle(FORBIDDEN), regen)
    assert outcome.regenerations == 2
    assert not outcome.manual_review


def test_exhausted_retries_flag_manual_review():
    calls = []

    def regen(_report, n):
        calls.append(n)
        return _bundle(FORBIDDEN)  # never fixes

    outcome = run_with_regeneration(_bundle(FORBIDDEN), regen)
    assert calls == [1, 2]                 # exactly max_retries regenerations
    assert outcome.regenerations == 2
    assert outcome.manual_review
    assert outcome.failures()              # violation list available for the digest


def test_regenerate_returning_none_gives_up_immediately():
    outcome = run_with_regeneration(_bundle(FORBIDDEN), lambda _r, _n: None)
    assert outcome.regenerations == 0
    assert outcome.manual_review


def test_warnings_do_not_trigger_regeneration():
    section = "The revenue was strong this quarter across regions."
    claim = EvidenceClaim(
        claim_id="c1", accession_number="a", section_key="mdna",
        char_start=0, char_end=5, snippet="strong",  # in text, but outside [0:5] -> warn
        text_sha256=hashlib.sha256(section.encode()).hexdigest(),
    )
    bundle = _bundle(CLEAN, evidence=[claim], section_texts={"a:mdna": section})
    regen_called = []
    outcome = run_with_regeneration(bundle, lambda _r, _n: regen_called.append(1))
    assert outcome.report.verdict == "PASS_WITH_WARNINGS"
    assert outcome.regenerations == 0 and not outcome.manual_review
    assert not regen_called


def test_store_sector_passed_through_to_verifier():
    outcome = run_with_regeneration(_bundle(CLEAN), lambda _r, _n: None,
                                    store=_store(1000.0, 600.0, 400.0), sector=GENERAL)
    v2a = [c for c in outcome.report.results if c.check_id == "V2a"]
    assert v2a and v2a[0].verdict == "pass"  # A = L + E enforced on the first run


def test_store_sector_held_constant_across_retries():
    # Initial run fails V5 (LLM-fixable) with a VALID store (V2a passes). After a
    # regeneration to clean text the FINAL report must still carry V2a=pass — proving
    # store/sector are threaded into the RETRY run_all, not only the first.
    outcome = run_with_regeneration(_bundle(FORBIDDEN), lambda _r, _n: _bundle(CLEAN),
                                    store=_store(1000.0, 600.0, 400.0), sector=GENERAL)
    assert outcome.regenerations == 1 and not outcome.manual_review
    v2a = [c for c in outcome.report.results if c.check_id == "V2a"]
    assert v2a and v2a[0].verdict == "pass"


def test_data_level_v2_failure_cannot_be_regenerated_away():
    # A broken accounting identity (A != L+E) fails V2a on EVERY run regardless of the
    # LLM text. Regeneration fixes the text but never the numbers, so it must exhaust
    # retries and route to manual review — a broken-accounting analysis never ships clean.
    # (Also fails immediately if the retry run_all drops store/sector: V2a would stop
    #  firing once V5 is fixed and the item would wrongly pass.)
    outcome = run_with_regeneration(_bundle(FORBIDDEN), lambda _r, _n: _bundle(CLEAN),
                                    store=_store(1000.0, 600.0, 300.0), sector=GENERAL)
    assert outcome.regenerations == 2
    assert outcome.manual_review
    assert any(c.check_id == "V2a" and c.verdict == "fail" for c in outcome.report.results)


# ---- persistence -----------------------------------------------------------
def test_persist_report_stores_every_checkresult():
    repo = Repo(init_db(":memory:"))
    report = run_all(_bundle(FORBIDDEN))
    n = persist_report(repo, analysis_id=1, report=report, created_at="t")
    rows = repo.list_verification_results(1)
    assert n == len(report.results) == len(rows)
    assert any(r.check_id == "V5" and r.verdict == "fail" and r.severity == "blocking"
               for r in rows)


def test_persist_report_round_trips_detail_and_created_at():
    # The manual-review 'violation list' is sourced from these rows, so detail must
    # survive — and must not be transposed with created_at.
    repo = Repo(init_db(":memory:"))
    report = run_all(_bundle(FORBIDDEN))
    persist_report(repo, analysis_id=1, report=report, created_at="ts-123")
    rows = repo.list_verification_results(1)

    v5_report = next(c for c in report.results if c.check_id == "V5" and c.verdict == "fail")
    v5_rows = [r for r in rows if r.check_id == "V5" and r.verdict == "fail"]
    assert len(v5_rows) == 1
    assert v5_rows[0].detail == v5_report.detail        # explanation survives (not None)
    assert "forbidden" in (v5_rows[0].detail or "").lower()
    assert all(r.created_at == "ts-123" for r in rows)  # timestamp column not transposed


def test_fact_values_from_repo():
    repo = Repo(init_db(":memory:"))
    repo.replace_xbrl_facts("1", [
        XbrlFact(cik="1", taxonomy="us-gaap", tag="Assets", value=100.0, instant="2024-01-01"),
        XbrlFact(cik="1", taxonomy="us-gaap", tag="Revenues", value=50.0,
                 period_start="2023-01-01", period_end="2023-12-31"),
    ])
    assert set(fact_values_from_repo(repo, "1")) == {100.0, 50.0}


def test_section_texts_from_repo():
    repo = Repo(init_db(":memory:"))
    repo.upsert_filing(Filing(accession_number="a-1", cik="1", form_type="10-K",
                              filed_at="2024-01-01"))
    repo.replace_filing_sections("a-1", [
        FilingSection(accession_number="a-1", section_key="mdna", text="MD&A body",
                      text_sha256="x"),
    ])
    assert section_texts_from_repo(repo, "a-1") == {"a-1:mdna": "MD&A body"}
