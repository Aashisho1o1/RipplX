"""Regression tests for the trust-layer fixes (verifier V1/V2/V5 + XBRL normalization).

Each test is named for the finding it guards. The dormant signal/valuation/adapter
regressions were removed with that research code; these cover only shipped behavior.
"""
from __future__ import annotations

from finwatch.core.types import DISCLAIMER
from finwatch.metrics.envelope import MetricResult, MetricsBundle, MetricStatus
from finwatch.verify.checks import (
    VerifyBundle,
    check_v5_hygiene,
    extract_number_tokens,
    run_all,
)


def _mr(metric, **kw):
    return MetricResult(metric=metric, status=MetricStatus.COMPUTED,
                        formula_version=f"{metric}.v1", as_of="t", **kw)


# ---- F8: V1 tokenizer is sign-aware (leading minus) --------------------------
def test_f8_leading_minus_is_negative_but_ranges_are_not():
    assert any(abs(t.value + 5.0) < 1e-9 for t in extract_number_tokens("Loss was -5%."))
    assert sorted(t.value for t in extract_number_tokens("a 5-10 range")) == [5.0, 10.0]


def test_f8_v1_flags_a_sign_reversed_number():
    m = MetricsBundle()
    m.results["x"] = _mr("x", value=5.0)                        # candidate +5
    bundle = VerifyBundle(rendered_text="Loss was -5%.", authored_text="", metrics=m,
                          disclaimer_text=DISCLAIMER, trade_action=None)
    v1 = next(c for c in run_all(bundle).results if c.check_id == "V1")
    assert v1.verdict == "fail"                                # -5 does not match +5


def test_v1_understands_unicode_minus_and_scientific_notation():
    unicode_minus = extract_number_tokens("Loss was −5%.")
    exponent = extract_number_tokens("Exposure was 1e9.")
    assert len(unicode_minus) == 1 and unicode_minus[0].value == -5.0
    assert len(exponent) == 1 and exponent[0].value == 1e9

    metrics = MetricsBundle(results={"x": _mr("x", value=1.0)})
    report = run_all(VerifyBundle(
        rendered_text="Exposure was 1e9.",
        authored_text="",
        metrics=metrics,
        disclaimer_text=DISCLAIMER,
    ))
    assert any(row.check_id == "V1" and row.verdict == "fail" for row in report.results)


def test_v1_exact_integer_does_not_use_blanket_relative_tolerance():
    metrics = MetricsBundle(results={"x": _mr("x", value=1_000_000_000.0)})
    report = run_all(VerifyBundle(
        rendered_text="Exposure was $1,000,400,000.",
        authored_text="",
        metrics=metrics,
        disclaimer_text=DISCLAIMER,
    ))
    assert any(row.check_id == "V1" and row.verdict == "fail" for row in report.results)


# ---- F9: V5 price-target regex covers the "$N target" form -------------------
def _v5(text):
    return check_v5_hygiene(VerifyBundle(
        rendered_text=text, authored_text=text, metrics=MetricsBundle(),
        disclaimer_text=DISCLAIMER, trade_action=None,
    ))


def test_f9_dollar_target_form_is_caught_without_false_positive():
    assert any(c.verdict == "fail" for c in _v5("We assign a $50 target."))
    assert any(c.verdict == "fail" for c in _v5("price target of $50"))
    assert all(c.verdict == "pass" for c in _v5("drifting above its target weight"))


def test_v5_blocks_trade_recommendations_and_authored_valuation_but_not_exact_quotes():
    assert any(c.verdict == "fail" for c in _v5("You should sell the shares."))
    assert any(c.verdict == "fail" for c in _v5("We estimate a fair value of $50."))
    quoted = VerifyBundle(
        rendered_text='The filing says “shareholders should sell the shares.”',
        authored_text="Material tender-offer terms disclosed",
        metrics=MetricsBundle(),
        disclaimer_text=DISCLAIMER,
    )
    assert all(c.verdict == "pass" for c in check_v5_hygiene(quoted))


# ---- F4: point-in-time — historical analyses never use future-filed facts ---
def test_f4_as_of_excludes_facts_filed_after_the_cutoff():
    from finwatch.metrics.service import as_of_facts
    from finwatch.xbrl.normalize import FactStore

    def fy(year, val, filed):   # revenue is a FLOW -> needs a full-year duration
        return {"start": f"{year}-01-01", "end": f"{year}-12-31", "val": val, "filed": filed,
                "fy": year, "fp": "FY", "form": "10-K"}

    cf = {"cik": "1", "entityName": "X", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"start": "2021-01-01", "end": "2021-12-31", "val": 50,
         "fy": 2021, "fp": "FY", "form": "10-K"},  # unprovable: no filed date
        fy(2022, 100, "2023-02-01"),
        fy(2023, 200, "2024-02-01"),
        fy(2024, 300, "2025-02-01"),   # filed AFTER the as_of below
    ]}}}}}
    filtered = as_of_facts(cf, "2024-06-01")
    kept = {e["end"] for e in filtered["facts"]["us-gaap"]["Revenues"]["units"]["USD"]}
    assert kept == {"2022-12-31", "2023-12-31"}  # future-filed and undated facts excluded
    store = FactStore.from_companyfacts(filtered)
    assert store.latest_annual("revenue").fact.value == 200.0   # not 300 (a future filing)


def test_companyfacts_unit_selection_is_deterministic_and_ambiguous_units_fail_closed():
    from finwatch.xbrl.normalize import FactStore

    def entry(end, value):
        year = int(end[:4])
        return {
            "start": f"{year}-01-01",
            "end": end,
            "val": value,
            "filed": f"{year + 1}-02-01",
            "accn": f"{year}",
            "form": "10-K",
            "fy": year,
            "fp": "FY",
        }

    units = {
        "EUR": [entry("2023-12-31", 900), entry("2022-12-31", 800)],
        "USD": [entry("2023-12-31", 200), entry("2022-12-31", 100)],
    }
    payload = {"facts": {"us-gaap": {"Revenues": {"units": units}}}}
    pair = FactStore.from_companyfacts(payload).yoy_pair("revenue")
    assert pair is not None
    assert [row.fact.unit for row in pair] == ["USD", "USD"]
    assert [row.fact.value for row in pair] == [200.0, 100.0]

    ambiguous = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "EUR": units["EUR"],
                        "GBP": [entry("2023-12-31", 700), entry("2022-12-31", 600)],
                    }
                }
            }
        }
    }
    assert FactStore.from_companyfacts(ambiguous).latest_annual("revenue") is None


# ---- F10: V2 accounting identities run (V2b annual-gated, V2a alignment-gated) --
def _bs(assets_end, liab_end, equity_end, assets, liab, equity):
    def inst(end, val):
        return {"end": end, "val": val, "filed": "2024-02-01", "fy": 2023, "fp": "FY",
                "form": "10-K"}
    return {"cik": "1", "entityName": "X", "facts": {"us-gaap": {
        "Assets": {"units": {"USD": [inst(assets_end, assets)]}},
        "Liabilities": {"units": {"USD": [inst(liab_end, liab)]}},
        "StockholdersEquity": {"units": {"USD": [inst(equity_end, equity)]}}}}}


def test_f10_v2_runs_with_annual_and_alignment_gates():
    from finwatch.core.types import sector_from_sic
    from finwatch.verify.orchestrator import data_quality_report
    from finwatch.xbrl.normalize import FactStore

    def audit(cf, form):
        store = FactStore.from_companyfacts(cf)
        return {r.check_id: r for r in data_quality_report(store, sector_from_sic("7372"),
                                                           form_type=form)}

    aligned = audit(_bs("2023-12-31", "2023-12-31", "2023-12-31", 100, 60, 40), "10-Q")
    assert aligned["V2a"].verdict == "pass"                          # A = L + E, same period
    assert aligned["V2b"].verdict == "skipped_not_applicable"        # 10-Q -> V2b n/a

    misaligned = audit(_bs("2023-12-31", "2023-12-31", "2022-12-31", 100, 60, 40), "10-Q")
    assert misaligned["V2a"].verdict == "skipped_not_applicable"     # different period-ends

    # A real, aligned imbalance is surfaced as a non-blocking WARNING, not a blocking fail:
    # V2 validates DATA and the raw identities false-fail on legitimate structures (NCI in
    # equity, restricted cash) too often to quarantine a whole filing on them.
    imbalanced = audit(_bs("2023-12-31", "2023-12-31", "2023-12-31", 100, 60, 50), "10-K")
    assert imbalanced["V2a"].verdict == "warn" and imbalanced["V2a"].severity == "warning"


def test_v2_data_quality_results_are_never_marked_blocking():
    from finwatch.core.types import sector_from_sic
    from finwatch.verify.orchestrator import data_quality_report
    from finwatch.xbrl.normalize import FactStore

    aligned = data_quality_report(
        FactStore.from_companyfacts(
            _bs("2023-12-31", "2023-12-31", "2023-12-31", 100, 60, 40)
        ),
        sector_from_sic("7372"),
        form_type="10-K",
    )
    assert all(result.severity != "blocking" for result in aligned)
    v2a = next(result for result in aligned if result.check_id == "V2a")
    assert v2a.verdict == "pass" and v2a.severity == "info"

    imbalanced = data_quality_report(
        FactStore.from_companyfacts(
            _bs("2023-12-31", "2023-12-31", "2023-12-31", 100, 60, 50)
        ),
        sector_from_sic("7372"),
        form_type="10-K",
    )
    v2a = next(result for result in imbalanced if result.check_id == "V2a")
    assert v2a.verdict == "warn" and v2a.severity == "warning"


def test_review_v2_nci_imbalance_does_not_block_the_filing():
    # A healthy consolidated issuer with noncontrolling interest (A = L + parent-equity +
    # NCI) breaks the parent-only A=L+E identity, but must NOT withhold — data-quality warning.
    from finwatch.core.types import sector_from_sic
    from finwatch.verify.checks import run_all
    from finwatch.verify.orchestrator import data_quality_report
    from finwatch.xbrl.normalize import FactStore

    store = FactStore.from_companyfacts(
        _bs("2024-12-31", "2024-12-31", "2024-12-31", 10000, 6000, 3600))  # 400 of NCI
    v2 = data_quality_report(store, sector_from_sic("7372"), form_type="10-K")
    assert {r.check_id for r in v2} >= {"V2a"}
    assert all(not (r.verdict == "fail" and r.severity == "blocking") for r in v2)
    llm_gate = run_all(VerifyBundle(
        rendered_text="", authored_text="", metrics=MetricsBundle(),
        disclaimer_text=DISCLAIMER, trade_action=None,
    ))
    combined = list(llm_gate.results) + list(v2)
    assert not any(c.verdict == "fail" and c.severity == "blocking" for c in combined)


# ---- F11: concept->tag resolves PER ACCESSOR (falls through to a usable tag) --
def test_f11_annual_falls_through_to_usable_fallback_tag():
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": "2024-02-01", "form": "10-Q"}

    cf = {"cik": "1", "facts": {"us-gaap": {
        # priority-1 revenue tag carries ONLY a quarter; the fallback carries the annuals
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            dur("2023-10-01", "2023-12-31", 50)]}},
        "Revenues": {"units": {"USD": [
            dur("2022-01-01", "2022-12-31", 180), dur("2023-01-01", "2023-12-31", 200)]}}}}}
    store = FactStore.from_companyfacts(cf)
    assert [r.fact.value for r in store.annual("revenue")] == [200.0, 180.0]   # fell through
    assert store.quarterly("revenue")[0].fact.value == 50.0                    # primary tag


def test_revenue_resolves_to_freshest_tag_across_a_tag_migration():
    # An issuer that migrated RevenueFromContract... → Revenues: the abandoned tag's
    # stale value must NOT be presented as current. Resolve to the freshest tag.
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": end, "form": "10-K"}

    cf = {"cik": "1", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            dur("2021-01-01", "2021-12-31", 300)]}},         # abandoned, stale
        "Revenues": {"units": {"USD": [
            dur("2023-01-01", "2023-12-31", 500),            # current
            dur("2022-01-01", "2022-12-31", 400)]}}}}}
    store = FactStore.from_companyfacts(cf)
    latest = store.latest_annual("revenue")
    assert latest.fact.tag == "Revenues" and latest.fact.value == 500.0
    cur, prior = store.yoy_pair("revenue")
    assert (cur.fact.value, prior.fact.value) == (500.0, 400.0)  # same tag, no splicing


def test_revenue_conflict_at_newest_period_fails_closed():
    # Two revenue tags claim the SAME newest period-end with DIFFERENT values (total vs a
    # contract-revenue subset). We cannot know which the user means → unavailable.
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": end, "form": "10-K"}

    cf = {"cik": "1", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            dur("2024-01-01", "2024-12-31", 300)]}},
        "Revenues": {"units": {"USD": [
            dur("2024-01-01", "2024-12-31", 999)]}}}}}
    assert FactStore.from_companyfacts(cf).latest_annual("revenue") is None


# ---- YoY annual pairing requires ~1yr spacing (no mislabeled multi-year delta) ----
def test_yoy_annual_pair_requires_one_year_spacing():
    from finwatch.xbrl.normalize import FactStore

    def dur(start, end, val):
        return {"start": start, "end": end, "val": val, "filed": end, "form": "10-K"}

    contiguous = {"cik": "1", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        dur("2022-01-01", "2022-12-31", 180),
        dur("2023-01-01", "2023-12-31", 200)]}}}}}
    pair = FactStore.from_companyfacts(contiguous).yoy_pair("revenue", "annual")
    assert pair is not None
    assert (pair[0].fact.value, pair[1].fact.value) == (200.0, 180.0)

    # Fiscal-year change: two annuals ~18 months apart must not be labelled "YoY".
    gapped = {"cik": "1", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        dur("2023-01-01", "2023-12-31", 180),
        dur("2024-07-01", "2025-06-30", 240)]}}}}}
    assert FactStore.from_companyfacts(gapped).yoy_pair("revenue", "annual") is None


# ---- V1 scale-branch gating: a coarse suffixed number cannot cross-scale-match ----
def _v1(text, m):
    bundle = VerifyBundle(rendered_text=text, authored_text="", metrics=m,
                          disclaimer_text=DISCLAIMER, trade_action=None)
    return next(c for c in run_all(bundle).results if c.check_id == "V1").verdict


def test_v1_coarse_billion_does_not_match_unrelated_fact_via_x100():
    m = MetricsBundle()
    m.results["exposure"] = _mr("exposure", value=30_000_000.0)     # unrelated $30M fact
    assert _v1("Exposure of roughly $3 billion.", m) == "fail"
    m2 = MetricsBundle()
    m2.results["exposure"] = _mr("exposure", value=2_700_000_000.0)  # $2.7B rounds to $3B
    assert _v1("Exposure of roughly $3 billion.", m2) == "pass"


def test_v1_percent_token_still_matches_ratio_fact_via_x100():
    m = MetricsBundle()
    m.results["rev_growth"] = _mr("rev_growth", value=0.081)         # ratio leaf
    assert _v1("Revenue grew 8.1%.", m) == "pass"


def test_v1_comma_grouped_count_in_year_range_is_not_whitelisted_as_year():
    toks = extract_number_tokens("The restructuring affects roughly 2,000 employees.")
    assert any(abs(t.value - 2000.0) < 1e-9 for t in toks)
    assert all(t.value != 2024.0 for t in extract_number_tokens("In fiscal 2024 revenue rose."))


def test_v1_reference_whitelist_is_anchored_not_substring():
    assert any(abs(t.value - 5.0) < 1e-9
               for t in extract_number_tokens("Item 2, and 5 sites were affected"))
    assert all(t.value != 2.0 for t in extract_number_tokens("see Item 2 of the report"))
