"""Shared test fixtures: in-memory DB + fixture-backed EDGAR/Stooq clients (no network)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from finwatch.db import Repo, init_db
from finwatch.ingest import EdgarClient, IngestService, StooqClient

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_fixture_json(name: str) -> dict:
    return json.loads(read_fixture(name))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def repo():
    conn = init_db(":memory:")
    try:
        yield Repo(conn)
    finally:
        conn.close()


def _fixture_handler(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "company_tickers.json" in u:
        return httpx.Response(200, text=read_fixture("company_tickers.json"))
    if "submissions/CIK0000320193" in u:
        return httpx.Response(200, text=read_fixture("submissions_CIK0000320193.json"))
    if "companyfacts/CIK0000320193" in u:
        return httpx.Response(200, text=read_fixture("companyfacts_CIK0000320193.json"))
    if "stooq.com" in u and "s=aapl.us" in u:
        return httpx.Response(200, text=read_fixture("stooq_aapl.csv"))
    return httpx.Response(404, text=f"no fixture for {u}")


@pytest.fixture
def mock_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_fixture_handler)


@pytest.fixture
def edgar_client(mock_transport) -> EdgarClient:
    return EdgarClient(
        "Test User test@example.com",
        client=httpx.Client(transport=mock_transport),
        sleep=lambda _s: None,
    )


@pytest.fixture
def stooq_client(mock_transport) -> StooqClient:
    return StooqClient(client=httpx.Client(transport=mock_transport))


@pytest.fixture
def ingest_service(repo, edgar_client, stooq_client) -> IngestService:
    # Fixed as_of so backfill-cutoff assertions are deterministic.
    return IngestService(repo, edgar_client, stooq_client, as_of=date(2024, 12, 1))
