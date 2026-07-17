"""Shared test fixtures: in-memory DB + fixture-backed EDGAR clients (no network)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from finwatch.db import Repo, init_db
from finwatch.ingest import EdgarClient, IngestService

FIXTURES = Path(__file__).parent / "fixtures"

# Config/credential vars a developer's real ./.env may define. load_config() reads .env
# via os.environ.setdefault, which persists for the whole pytest process once any CLI
# test triggers it — leaking a real key into unrelated tests (e.g. provider-readiness
# assertions). Clear them before every test so the suite is hermetic and matches a
# no-.env baseline; tests set what they need explicitly.
_LEAKY_ENV = (
    "SEC_USER_AGENT",
    "FINWATCH_DB",
    "FINWATCH_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENROUTER_API_KEY",
    "FINWATCH_AUTH_SECRET",
    "FINWATCH_ALLOWED_HOSTS",
    "FINWATCH_EMAIL_FROM",
    "RESEND_API_KEY",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for name in _LEAKY_ENV:
        monkeypatch.delenv(name, raising=False)


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
def ingest_service(repo, edgar_client) -> IngestService:
    # Fixed as_of so backfill-cutoff assertions are deterministic.
    return IngestService(repo, edgar_client, as_of=date(2024, 12, 1))
