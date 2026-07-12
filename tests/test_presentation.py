from finwatch.db import Computation, Filing, Repo
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
        assert repo.get_company(company.cik).tracked_at is None
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


def test_share_count_display_is_neutral_and_tiny_changes_render_flat():
    base = MetricResult(
        metric="share_count_change",
        status="computed",
        value=-0.000428,
        formula_version="share_count_change.v2",
        as_of="2024-04-25",
    )

    values = (
        format_metric_value(base),
        format_metric_value(base.model_copy(update={"value": -0.01})),
        format_metric_value(base.model_copy(update={"value": 0.01})),
    )
    assert values == (
        "0.0% YoY (share count flat)",
        "-1.0% YoY (share count decreased)",
        "+1.0% YoY (share count increased)",
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
