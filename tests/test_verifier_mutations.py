"""Mutation battery — the verifier's Definition of Done. Trust-critical (test-guarded): edit with care, keep the spec tests green.
Builds a known-good bundle, then seeds corruptions (V1 provenance, V2a identity,
V4 citation, V5 hygiene); each must FAIL on the correct check id, and the clean
bundle must PASS."""
from __future__ import annotations

import hashlib

import pytest

from finwatch.core.types import DISCLAIMER, MetricStatus, SectorClass, SectorInfo
from finwatch.metrics.envelope import MetricResult, MetricsBundle
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
                char=(16, 32), authored="Revenue changed year over year") -> VerifyBundle:
    metrics = MetricsBundle()
    metrics.results["revenue_growth"] = MetricResult(
        metric="revenue_growth", status=MetricStatus.COMPUTED, value=0.12,
        components={"yoy": 0.12}, formula_version="revenue_growth.v1", as_of=AS_OF)
    rendered = rendered or ("Revenue grew 0.12 year over year; the filing cites "
                            "$1,234.5 million of net revenue.")
    return VerifyBundle(
        rendered_text=rendered,
        authored_text=authored,
        metrics=metrics,
        fact_store_values=[1_234.5e6, 87.0e6],
        evidence_claims=[EvidenceClaim(
            claim_id="c_0001", accession_number="0000000000-26-000001",
            section_key="mdna", char_start=char[0], char_end=char[1],
            snippet=snippet,
            text_sha256=hashlib.sha256(SECTION.encode()).hexdigest())],
        section_texts={KEY: SECTION},
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


def test_mutation_e_price_target_language_fails_v5():
    b = make_bundle(
        rendered="Revenue grew 0.12; we see a price target of $50.",
        authored="We see a price target",
    )
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V5" in failing_ids(r)


@pytest.mark.parametrize(
    ("confidence", "gaps"),
    [
        ("low", []),
        ("high", ["Input was truncated before controls."]),
    ],
)
def test_model_reported_incomplete_extraction_is_nonblocking(
    confidence: str, gaps: list[str]
):
    b = make_bundle()
    b.extraction_confidence = confidence
    b.extraction_gaps = gaps
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V5" not in failing_ids(r)


@pytest.mark.parametrize(
    "authored",
    [
        "Revenue increased a dozen basis points",
        "Revenue moved by 25 bps",
        "Margins moved ½ a point",
        "Revenue changed by a fraction",
        "Investors should avoid the shares",
        "The price target increased",
        "We estimate a fair value for the shares",
        "Guaranteed upside",
        "Exit the position",
        "Reduce exposure to the shares",
        "Stay away from the stock",
    ],
)
def test_authored_quantity_or_advice_bypass_fails_v5(authored: str):
    b = make_bundle()
    b.authored_text = authored
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V5" in failing_ids(r)


def test_verify_bundle_requires_an_explicit_authored_subset():
    with pytest.raises(ValueError):
        VerifyBundle(rendered_text="Exact filing quote: $50", metrics=MetricsBundle())


def test_bank_income_ordering_is_skipped_not_failed():
    results = check_v2_identities(make_store(),
                                  SectorInfo(SectorClass.FINANCIAL, True))
    v2c = [c for c in results if c.check_id == "V2c"]
    assert v2c and v2c[0].verdict == "skipped_not_applicable"


# --- V1 whitelist regressions: references/dates must not orphan-fail V1 -------
# Before these fixes, before.strip() defeated the "Item " whitelist and the date
# window was too short, so the most material filings (8-K Item 4.02, auditor
# resignations dated in prose) blocking-failed V1 and were routed to manual review.

def test_item_code_reference_is_not_orphaned_v1():
    b = make_bundle(rendered="Non-reliance on prior financials (Item 4.02); "
                             "see also Item 1.03 and Item 2.04.")
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V1" not in failing_ids(r), \
        [x.detail for x in r.results if x.check_id == "V1"]


def test_calendar_dates_are_not_orphaned_v1():
    b = make_bundle(rendered="Auditor resigned effective March 15, 2024; the "
                             "restatement was disclosed on 2024-09-28.")
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V1" not in failing_ids(r), \
        [x.detail for x in r.results if x.check_id == "V1"]


def test_real_orphan_still_fails_v1_next_to_a_date():
    # Guard the fix in the other direction: the date whitelist stays tight — a
    # genuine unprovenanced figure sitting beside a date must STILL fail V1.
    b = make_bundle(rendered="On March 15, 2024 the filing cited $999.9 million "
                             "of unexplained charges.")
    r = run_all(b, make_store(), SectorInfo(SectorClass.GENERAL, False))
    assert "V1" in failing_ids(r)
