import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.db import Repo, init_db
from finwatch.demo import build_demo_db
from finwatch.web.app import JobRequest, _compute_synced_metrics, create_app
from finwatch.web.runtime import SETTING_SIGNALS


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "finwatch.db"
    app = create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist")
    return TestClient(app), db_path


def test_web_analysis_request_is_bounded_to_one_filing_by_default():
    assert JobRequest().limit == 1
    with pytest.raises(ValueError):
        JobRequest(limit=11)


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


def test_demo_contract_and_shadow_default_off(tmp_path):
    client, _ = _client(tmp_path)
    response = client.get("/api/brief?demo=true&include_signals=true")
    assert response.status_code == 200
    body = response.json()
    assert body["sample_data"] is True
    assert body["critical_red_flags"][0]["flags"][0]["edgar_url"].startswith("https://")
    assert body["shadow_signals"]  # demo explicitly requested signals

    live = client.get("/api/brief?include_signals=true").json()
    assert live["shadow_signals"] == []


def test_live_shadow_requires_persisted_opt_in(tmp_path):
    client, db_path = _client(tmp_path)
    conn = init_db(str(db_path))
    try:
        Repo(conn).set_setting(SETTING_SIGNALS, "true")
    finally:
        conn.close()
    assert client.get("/api/bootstrap").json()["signals"] is True


def test_mutation_rejects_foreign_origin(tmp_path):
    client, _ = _client(tmp_path)
    response = client.put(
        "/api/settings",
        headers={"Origin": "https://malicious.example"},
        json={"signals": True},
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
