"""Live EDGAR/Stooq smoke test — excluded by default (run with `-m live`).

Requires network and a real SEC_USER_AGENT. Verifies the ingest pipeline against
the live SEC + Stooq endpoints for a stable large-cap (AAPL, CIK 0000320193).
"""
from __future__ import annotations

import os

import pytest

from finwatch.db import Repo, init_db
from finwatch.ingest import EdgarClient, IngestService, StooqClient

CIK = "0000320193"


@pytest.mark.live
def test_live_ingest_aapl():
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        pytest.skip("SEC_USER_AGENT not set")

    conn = init_db(":memory:")
    repo = Repo(conn)
    service = IngestService(repo, EdgarClient(user_agent), StooqClient())

    service.add_holding("AAPL", owned=False)
    summary = service.ingest_all(backfill_quarters=4)

    assert summary.results and summary.results[0].error is None, summary.results
    company = repo.get_company(CIK)
    assert company is not None and company.sic_code  # profile populated
    assert repo.count_xbrl_facts(CIK) > 0
    assert len(repo.list_filings(CIK)) > 0
    assert repo.count_prices("AAPL") > 0
