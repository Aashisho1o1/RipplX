from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.db import Repo, init_db
from finwatch.web.app import create_app
from finwatch.web.runtime import SETTING_SIGNALS


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "finwatch.db"
    app = create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist")
    return TestClient(app), db_path


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
