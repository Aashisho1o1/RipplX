from finwatch.db import Repo
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.presentation import PresentationService


def test_demo_projection_preserves_digest_order_and_trust_data():
    conn = build_demo_db()
    try:
        view = PresentationService(Repo(conn)).brief(
            since=DEMO_SINCE, include_signals=False, sample_data=True
        )
    finally:
        conn.close()

    assert view.answer == "One holding needs a critical review."
    assert view.period.filings_in_window == 5
    assert view.period.analyzed_filings == 5
    assert [item.ticker for item in view.critical_red_flags] == ["TWKS", "DPLS"]
    assert view.critical_red_flags[1].flags[0].quote
    assert [row.ticker for row in view.verified_numbers] == ["DPLS", "MSFT"]
    assert view.shadow_signals == []
    assert view.boring_filings == (
        "3 routine filing(s) with no material findings (AAPL 8-K, MSFT 10-Q, AAPL 10-Q)."
    )


def test_metrics_as_of_never_uses_future_computation():
    conn = build_demo_db()
    try:
        service = PresentationService(Repo(conn))
        before = service.metrics("MSFT", as_of="2024-07-01")
        current = service.metrics("MSFT", as_of="2024-08-05")
    finally:
        conn.close()

    assert before is not None and before.before_first_filing and before.rows == []
    assert current is not None and current.rows[0].state == "computed"


def test_removing_holding_retains_company_and_filings():
    conn = build_demo_db()
    try:
        repo = Repo(conn)
        company = repo.get_company_by_ticker("DPLS")
        assert company is not None
        filings_before = repo.list_filings(company.cik)
        assert repo.delete_holding(company.cik)
        assert repo.get_holding_by_cik(company.cik) is None
        assert repo.get_company(company.cik) is not None
        assert repo.list_filings(company.cik) == filings_before
    finally:
        conn.close()
