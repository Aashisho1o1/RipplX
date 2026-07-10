import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.db import Repo, init_db
from finwatch.demo import build_demo_db
from finwatch.web.app import HoldingCreate, JobRequest, _compute_synced_metrics, create_app


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "finwatch.db"
    app = create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist")
    return TestClient(app), db_path


def test_web_job_request_accepts_only_an_optional_ticker():
    assert JobRequest().ticker is None
    assert JobRequest(ticker="MSFT").ticker == "MSFT"
    for legacy in ({"limit": 2}, {"form": "10-Q"}, {"accession": "a-1"}, {"mode": "parse"}):
        with pytest.raises(ValueError):
            JobRequest(**legacy)


def test_holding_create_is_ticker_only_and_legacy_rows_do_not_leak_private_fields(tmp_path):
    assert HoldingCreate(ticker="BRK-B").ticker == "BRK-B"
    for legacy in (
        {"ticker": "MSFT", "shares": 10},
        {"ticker": "MSFT", "cost_basis": 100},
        {"ticker": "MSFT", "target_weight_pct": 5},
        {"ticker": "MSFT", "thesis": "private"},
        {"ticker": "MSFT", "owned": False},
    ):
        with pytest.raises(ValueError):
            HoldingCreate(**legacy)

    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    client = TestClient(create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"))
    rows = client.get("/api/holdings").json()["owned"]
    assert rows
    assert {"shares", "cost_basis", "target_weight_pct", "horizon", "thesis"}.isdisjoint(
        rows[0]
    )
    assert client.patch("/api/holdings/MSFT", json={"shares": 1}).status_code == 404


def test_analyze_endpoint_rejects_historical_replay_controls(tmp_path):
    client, _ = _client(tmp_path)
    for payload in ({"limit": 10}, {"form": "10-Q"}, {"accession": "a-1"}, {"mode": "parse"}):
        assert client.post("/api/jobs/analyze", json=payload).status_code == 422


def test_bootstrap_setup_and_session_key_are_safe(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    client, db_path = _client(tmp_path)
    assert client.get("/api/bootstrap").json()["setup_required"] is True

    response = client.put(
        "/api/settings",
        json={
            "sec_user_agent": "Test User test@example.com",
            "model_extract": "openai/test",
            "api_key": "secret-value",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["setup_required"] is False
    assert body["api_key_configured"] is True
    assert body["api_key_source"] == "session"
    assert "secret-value" not in response.text

    conn = init_db(str(db_path))
    try:
        settings = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()
    assert "secret-value" not in settings.values()


def test_restart_keeps_portfolio_results_but_drops_session_key(tmp_path, monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "AZURE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    first = TestClient(create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"))
    response = first.put(
        "/api/settings",
        json={
            "sec_user_agent": "Test User test@example.com",
            "model_extract": "openai/test",
            "model_reason": "openai/test",
            "api_key": "disposable-secret",
        },
    )
    assert response.json()["api_key_source"] == "session"
    assert len(first.get("/api/holdings").json()["owned"]) == 2

    restarted = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist")
    )
    bootstrap = restarted.get("/api/bootstrap").json()
    assert bootstrap["sec_user_agent"] == "Test User test@example.com"
    assert bootstrap["model_extract"] == "openai/test"
    assert bootstrap["api_key_configured"] is False
    assert len(restarted.get("/api/holdings").json()["owned"]) == 2
    filing = restarted.get("/api/filings/0001683168-24-004848")
    assert filing.status_code == 200
    assert filing.json()["verification"] is not None
    pipeline = {stage["stage"]: stage["status"] for stage in filing.json()["pipeline"]}
    assert pipeline["parse"] == "completed"
    assert pipeline["verify"] == "completed"


def test_demo_contract_has_no_shadow_surface(tmp_path):
    client, _ = _client(tmp_path)
    response = client.get("/api/brief?demo=true&include_signals=true")
    assert response.status_code == 200
    body = response.json()
    assert body["sample_data"] is True
    assert body["critical_red_flags"][0]["flags"][0]["edgar_url"].startswith("https://")
    assert "shadow_signals" not in body
    assert "signals" not in client.get("/api/bootstrap").json()


def test_removed_track_record_api_returns_json_404(tmp_path):
    client, _ = _client(tmp_path)
    response = client.get("/api/track-record")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "api_route_not_found"


def test_removed_reverify_endpoint_cannot_replace_audit_rows(tmp_path):
    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    conn = init_db(str(db_path))
    repo = Repo(conn)
    analysis = repo.latest_analysis("0001683168-24-004848", "P1")
    assert analysis is not None
    before = repo.list_verification_results(analysis.id)
    conn.close()

    client = TestClient(create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"))
    response = client.post("/api/filings/0001683168-24-004848/reverify")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "api_route_not_found"

    conn = init_db(str(db_path))
    try:
        after = Repo(conn).list_verification_results(analysis.id)
    finally:
        conn.close()
    assert after == before


def test_mutation_rejects_foreign_origin(tmp_path):
    client, _ = _client(tmp_path)
    response = client.put(
        "/api/settings",
        headers={"Origin": "https://malicious.example"},
        json={"period": "30d"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


def test_frontend_dist_can_be_configured_for_packaged_deployment(tmp_path, monkeypatch):
    dist = tmp_path / "web-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>packaged RipplX</main>", encoding="utf-8")
    monkeypatch.setenv("FINWATCH_WEB_DIST", str(dist))

    app = create_app(db_path=str(tmp_path / "finwatch.db"))
    response = TestClient(app).get("/brief")

    assert response.status_code == 200
    assert "packaged RipplX" in response.text


def test_web_sync_computes_and_persists_verified_metrics(tmp_path):
    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    conn = init_db(str(db_path))
    try:
        repo = Repo(conn)
        before = len(repo.list_computations("MSFT"))
        facts_path = (
            Path(__file__).parents[1]
            / "src"
            / "finwatch"
            / "demo"
            / "data"
            / "companyfacts_MSFT.json"
        )
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        service = SimpleNamespace(
            repo=repo,
            edgar=SimpleNamespace(companyfacts=lambda _cik: facts),
        )

        computed = _compute_synced_metrics(
            service, "0000789019", as_of="2024-08-05"
        )

        assert computed > 0
        assert len(repo.list_computations("MSFT")) > before
    finally:
        conn.close()
