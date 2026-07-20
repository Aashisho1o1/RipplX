import json

from finwatch.db import LOCAL_USER_ID, Company, Computation, Filing, Repo, init_db
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.metrics.catalog import STARTER_METRICS
from finwatch.metrics.envelope import MetricResult
from finwatch.presentation import PresentationService, models
from finwatch.presentation.formatting import format_metric_value


def test_demo_projection_preserves_digest_order_and_trust_data():
    conn = build_demo_db()
    try:
        view = PresentationService(Repo(conn)).brief(since=DEMO_SINCE, sample_data=True)
    finally:
        conn.close()

    assert view.answer == "A tracked company needs a critical review."
    assert view.period.filings_in_window == 5
    assert view.period.analyzed_filings == 5
    assert [item.ticker for item in view.filings] == ["DPLS", "MSFT", "TWKS"]
    assert view.filings[0].findings[0].evidence[0].quote
    assert all(len(item.findings) <= 3 for item in view.filings)
    assert [row.ticker for row in view.verified_numbers] == ["AAPL", "DPLS", "MSFT", "TWKS"]
    assert [(entry.ticker, entry.form) for entry in view.reviewed_filings] == [
        ("AAPL", "8-K"), ("AAPL", "10-Q")
    ]
    assert view.period.published_filings == 5
    assert view.period.filings_tracked_total == 5


def test_gate_removed_findings_are_not_reported_as_nothing_changed():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        trace = repo.latest_analysis("0000320193-24-000081", "P1_TRACE")
        assert trace is not None and trace.id is not None
        payload = json.loads(trace.output_json)
        payload["dropped_findings"] = [
            {"finding_id": "f1", "error_codes": ["QUOTE_NOT_EXACT"]}
        ]
        conn.execute(
            "UPDATE analyses SET output_json = ? WHERE id = ?",
            (json.dumps(payload), trace.id),
        )
        conn.commit()

        brief = PresentationService(repo).brief(since=DEMO_SINCE)
        assert [row.accession for row in brief.gate_removed_filings] == [
            "0000320193-24-000081"
        ]
        entry = brief.gate_removed_filings[0]
        assert entry.dropped_finding_count == 1
        assert entry.outcome == "findings_dropped"
        assert any("failed the evidence gate" in question for question in brief.open_questions)
    finally:
        conn.close()


def test_critical_finding_outranks_a_pipeline_failure_in_the_headline():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        repo.upsert_filing(
            Filing(
                accession_number="0000320193-26-000099",
                cik="0000320193",
                form_type="8-K",
                filed_at="2026-05-01",
                status="failed",
            )
        )
        brief = PresentationService(repo).brief(since=DEMO_SINCE)
        assert brief.answer.startswith("A tracked company needs a critical review.")
        assert "could not be analyzed" in brief.answer
        assert not brief.answer.startswith("1 filing could not be analyzed")
    finally:
        conn.close()


def test_brief_names_the_filing_that_sits_outside_the_reading_window():
    conn = build_demo_db()
    try:
        brief = PresentationService(Repo(conn)).brief(since="2099-01-01")
        assert brief.period.filings_in_window == 0
        assert brief.period.filings_tracked_total == 5
        assert brief.period.covered_label == "1 Jan 2099 → today"
        assert "AAPL 8-K filed 30 Apr 2026" in (brief.period.outside_window or "")
        assert "Settings" in (brief.period.outside_window or "")
        assert brief.answer == "No tracked filing falls inside your reading window."
        assert brief.tracked_but_unanalyzed is False
    finally:
        conn.close()


def test_routine_filings_publish_as_linkable_reviewed_entries():
    conn = build_demo_db()
    try:
        view = PresentationService(Repo(conn)).brief(since=DEMO_SINCE)
        assert [(entry.ticker, entry.form) for entry in view.reviewed_filings] == [
            ("AAPL", "8-K"),
            ("AAPL", "10-Q"),
        ]
        assert all(not entry.findings and not entry.withheld for entry in view.reviewed_filings)
        assert all(
            entry.accession and entry.edgar_url.startswith("https://www.sec.gov/")
            for entry in view.reviewed_filings
        )
        assert not hasattr(view, "boring_filings")
    finally:
        conn.close()


def test_brief_contract_carries_no_posture_field():
    conn = build_demo_db()
    try:
        view = PresentationService(Repo(conn)).brief(since=DEMO_SINCE)
        assert "answer_posture" not in models.BriefView.model_fields
        assert not hasattr(models, "Posture")
        assert not any("posture" in key for key in view.model_dump())
        assert view.answer == "A tracked company needs a critical review."
    finally:
        conn.close()


def test_companies_newest_filing_ignores_unsupported_forms():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        repo.upsert_filing(
            Filing(
                accession_number="0000320193-26-000444",
                cik="0000320193",
                form_type="4",
                filed_at="2026-06-30",
            )
        )
        apple = next(
            row for row in PresentationService(repo).companies().companies
            if row.ticker == "AAPL"
        )
        assert apple.newest_supported_filing == "2026-04-30"
    finally:
        conn.close()


def test_all_unavailable_metrics_still_render_a_summarized_table():
    conn = init_db(":memory:")
    try:
        repo = Repo(conn)
        for cik, ticker in (("0000000001", "ZZZ"), ("0000000002", "WWW")):
            repo.upsert_company(
                Company(
                    cik=cik,
                    ticker=ticker,
                    name=ticker,
                    added_at="2026-07-20T00:00:00Z",
                )
            )
            repo.track_company(cik, at="2026-07-20T00:00:00Z")
        unavailable = MetricResult(
            metric="revenue_growth",
            status="unavailable",
            unavailable_missing=["us-gaap:Revenues"],
            formula_version="revenue_growth.v5",
            as_of="2026-06-30",
        )
        repo.insert_computations(
            [
                Computation(
                    ticker="ZZZ",
                    tool="revenue_growth",
                    args_json="{}",
                    result_json=unavailable.model_dump_json(),
                    status="unavailable",
                    formula_version="revenue_growth.v5",
                    as_of="2026-06-30",
                    created_at="2026-07-20T00:00:00Z",
                )
            ]
        )
        brief = PresentationService(repo).brief()
        by_ticker = {row.ticker: row for row in brief.verified_numbers}
        assert by_ticker["ZZZ"].empty is None
        assert len(by_ticker["ZZZ"].rows) == 1
        assert by_ticker["ZZZ"].rows[0].state == "unavailable"
        assert "us-gaap:Revenues" in by_ticker["ZZZ"].rows[0].state_label
        assert by_ticker["ZZZ"].summary == "1 unavailable of 6 starter metrics"
        assert by_ticker["WWW"].rows == []
        assert by_ticker["WWW"].empty == (
            "No SEC XBRL metric has been computed for this issuer yet."
        )
        assert "insufficient" not in (by_ticker["WWW"].empty or "").lower()
    finally:
        conn.close()


def test_compressed_read_denominator_is_the_fixed_starter_catalog():
    conn = build_demo_db()
    try:
        company = next(
            row for row in PresentationService(Repo(conn)).companies().companies
            if row.ticker == "MSFT"
        )
        assert company.compressed_verified_read is not None
        assert company.compressed_verified_read.endswith(f"/{len(STARTER_METRICS)}")
        assert "/5" not in company.compressed_verified_read
    finally:
        conn.close()


def test_filing_verification_projects_check_ids_and_only_v2_details():
    conn = build_demo_db()
    try:
        detail = PresentationService(Repo(conn)).filing("0000950170-24-048288")
        assert detail is not None and detail.verification is not None
        checks = detail.verification.checks
        assert [row.check_id for row in checks] == [
            "V1", "V4", "V5", "V2a", "V2b", "V2c", "V2d"
        ]
        assert detail.verification.verdict == "PASS"
        by_id = {row.check_id: row for row in checks}
        assert by_id["V2c"].detail == (
            "rev=168088000000.0 gp=115856000000.0 oi=69916000000.0"
        )
        assert (by_id["V2a"].detail or "").startswith(
            "assets/liabilities/equity resolved to different period-ends"
        )
        assert (by_id["V2b"].detail or "").startswith(
            "cash tie-out compares the fiscal-year change"
        )
        assert all(by_id[check_id].detail is None for check_id in ("V1", "V4", "V5"))
    finally:
        conn.close()


def test_metrics_as_of_never_uses_future_computation():
    conn = build_demo_db()
    try:
        service = PresentationService(Repo(conn))
        before = service.metrics("MSFT", as_of="2024-04-01")
        current = service.metrics("MSFT", as_of="2024-08-05")
    finally:
        conn.close()

    assert before is not None and before.before_first_filing and before.rows == []
    assert current is not None
    rows = {row.metric: row for row in current.rows}
    assert rows["Revenue growth"].state == "unavailable"
    assert "current source is stale" in rows["Revenue growth"].state_label
    assert rows["Liquidity"].state == "computed"


def test_untracking_retains_company_and_filings():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        company = repo.get_company_by_ticker("DPLS")
        assert company is not None
        filings_before = repo.list_filings(company.cik)
        assert repo.untrack_company(company.cik)
        assert repo.get_user_company(LOCAL_USER_ID, company.cik) is None
        assert repo.get_company(company.cik) is not None
        assert repo.list_filings(company.cik) == filings_before
        brief = PresentationService(repo).brief(since=DEMO_SINCE)
        presented = brief.filings + brief.withheld_filings
        assert all(entry.ticker != "DPLS" for entry in presented)
        assert all(entry.ticker != "DPLS" for entry in brief.reviewed_filings)
        assert brief.period.filings_in_window == 4
    finally:
        conn.close()


def test_brief_excludes_unsupported_forms_for_tracked_companies():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        repo.upsert_filing(
            Filing(
                accession_number="unsupported-form-4",
                cik="0000789019",
                form_type="4",
                filed_at="2024-08-06",
            )
        )

        brief = PresentationService(repo).brief(since=DEMO_SINCE)

        assert brief.period.filings_in_window == 5
        accessions = {
            entry.accession
            for entry in brief.filings + brief.withheld_filings
        }
        assert "unsupported-form-4" not in accessions
    finally:
        conn.close()


def test_share_count_display_does_not_certify_an_unavailable_direction():
    base = MetricResult(
        metric="share_count_change",
        status="computed",
        value=-0.000428,
        formula_version="share_count_change.v4",
        as_of="2024-04-25",
    )
    values = (
        format_metric_value(base),
        format_metric_value(base.model_copy(update={"value": -0.01})),
        format_metric_value(base.model_copy(update={"value": 0.01})),
    )
    assert values == tuple(
        f"{value} YoY (direction not certified within SEC rounding slack)"
        for value in ("0.0%", "-1.0%", "+1.0%")
    )
    assert all(word not in " ".join(values).lower() for word in ("buyback", "dilution"))



def test_simple_leverage_is_labeled_as_an_accounting_proxy():
    result = MetricResult(
        metric="simple_leverage",
        status="computed",
        value=2.0,
        components={"net_debt_to_ebitda": 2.0, "interest_coverage": 4.0},
        formula_version="simple_leverage.v2",
        as_of="2024-04-25",
    )

    assert format_metric_value(result) == (
        "net debt / (operating income + D&A) proxy 2.00× · interest coverage 4.00×"
    )
    assert "EBITDA" not in format_metric_value(result)


def test_computed_metric_without_typed_sec_inputs_is_withheld_not_hidden():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        tampered = MetricResult(
            metric="liquidity_basics",
            status="computed",
            components={"cash": 999_000_000.0, "net_debt": 0.0},
            formula_version="liquidity_basics.v2",
            as_of="2024-04-25",
            inputs_used=[],
        )
        repo.insert_computations(
            [
                Computation(
                    ticker="MSFT",
                    tool="liquidity_basics",
                    args_json="{}",
                    result_json=tampered.model_dump_json(),
                    status="computed",
                    formula_version="liquidity_basics.v2",
                    as_of="2024-04-25",
                    created_at="later",
                )
            ]
        )

        view = PresentationService(repo).metrics("MSFT", as_of="2024-04-25")
        assert view is not None
        row = next(row for row in view.rows if row.metric == "Liquidity")
        assert row.state == "withheld"
        assert row.value == "— withheld"
        assert "999" not in view.model_dump_json()
    finally:
        conn.close()
