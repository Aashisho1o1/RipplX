from finwatch.db import LOCAL_USER_ID, Computation, Filing, Repo
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.metrics.envelope import MetricResult
from finwatch.presentation import PresentationService
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
    assert view.boring_filings == (
        "2 routine filing(s) with no material findings (AAPL 8-K, AAPL 10-Q)."
    )


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
        assert "DPLS" not in (brief.boring_filings or "")
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


def test_share_count_display_follows_the_deterministic_direction():
    """Displayed direction must be the gate's verdict, not a second heuristic.

    This previously asserted the output of a hard-coded +/-0.0005 band applied to the
    displayed percentage, on a fixture that carried no direction metadata at all. That
    pinned a divergence: the deterministic gate judges direction against the SEC
    `decimals` rounding slack, so it could prove a decrease on a filing whose displayed
    percentage rounds to 0.0% and the page would still read "share count flat" — while
    a model finding claiming "flat" was dropped as METRIC_CONTRADICTION on the same page.
    """
    base = MetricResult(
        metric="share_count_change",
        status="computed",
        value=-0.000428,
        formula_version="share_count_change.v4",
        as_of="2024-04-25",
    )
    flat = base.model_copy(update={"direction_delta": 0.0, "direction_slack": 1000.0})
    down = base.model_copy(
        update={"value": -0.01, "direction_delta": -4_000_000.0, "direction_slack": 1.0}
    )
    up = base.model_copy(
        update={"value": 0.01, "direction_delta": 4_000_000.0, "direction_slack": 1.0}
    )

    values = (
        format_metric_value(flat),
        format_metric_value(down),
        format_metric_value(up),
    )
    assert values == (
        "0.0% YoY (share count flat)",
        "-1.0% YoY (share count decreased)",
        "+1.0% YoY (share count increased)",
    )
    assert all(word not in " ".join(values).lower() for word in ("buyback", "dilution"))

    # A proven decrease whose displayed percentage rounds to 0.0% must not read "flat".
    proven_down_but_tiny = base.model_copy(
        update={"direction_delta": -4_000_000.0, "direction_slack": 1.0}
    )
    assert format_metric_value(proven_down_but_tiny) == "0.0% YoY (share count decreased)"

    # Without direction metadata the gate has no opinion and the display falls back to
    # the value. This path is the PRODUCTION path: the SEC companyfacts API ships no
    # `decimals` (zero occurrences across every cached issuer payload and recorded
    # fixture), so deferring to deterministic_direction alone would delete the
    # direction clause for every real issuer.
    assert format_metric_value(base) == "0.0% YoY (share count flat)"
    assert format_metric_value(
        base.model_copy(update={"value": -0.017})
    ) == "-1.7% YoY (share count decreased)"


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


def test_computed_metric_without_typed_sec_inputs_is_not_rendered():
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
        assert "Liquidity" not in {row.metric for row in view.rows}
    finally:
        conn.close()
