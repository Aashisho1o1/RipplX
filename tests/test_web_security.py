"""Hosted-alpha authentication and host-boundary regression tests."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.web.app import create_app

TOKEN = "a" * 32


def test_remote_app_refuses_missing_or_weak_security_configuration(tmp_path):
    kwargs = {"db_path": str(tmp_path / "db.sqlite"), "web_dist": tmp_path / "missing"}
    with pytest.raises(RuntimeError, match="AUTH_TOKEN"):
        create_app(remote=True, allowed_hosts=["alpha.example"], **kwargs)
    with pytest.raises(RuntimeError, match="at least 32"):
        create_app(
            remote=True,
            auth_token="too-short",
            allowed_hosts=["alpha.example"],
            **kwargs,
        )
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        create_app(remote=True, auth_token=TOKEN, allowed_hosts=[], **kwargs)
    with pytest.raises(RuntimeError, match=r"never '\*'"):
        create_app(remote=True, auth_token=TOKEN, allowed_hosts=["*"], **kwargs)


def test_remote_api_requires_bearer_token_but_health_is_public(tmp_path):
    app = create_app(
        db_path=str(tmp_path / "db.sqlite"),
        web_dist=tmp_path / "missing",
        remote=True,
        auth_token=TOKEN,
        allowed_hosts=["alpha.example"],
    )
    client = TestClient(app, base_url="https://alpha.example")

    assert client.get("/healthz").json() == {"status": "ok"}
    missing = client.get("/api/bootstrap")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.json()["error"]["code"] == "authentication_required"
    assert client.get(
        "/api/bootstrap", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    assert client.get(
        "/api/bootstrap", headers={"Authorization": f"Bearer {TOKEN}"}
    ).status_code == 200


def test_remote_host_allowlist_applies_before_api_use(tmp_path):
    app = create_app(
        db_path=str(tmp_path / "db.sqlite"),
        web_dist=tmp_path / "missing",
        remote=True,
        auth_token=TOKEN,
        allowed_hosts=["alpha.example"],
    )
    response = TestClient(app, base_url="https://attacker.example").get(
        "/api/bootstrap", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 400


def test_local_api_remains_auth_free(tmp_path):
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    assert TestClient(app).get("/api/bootstrap").status_code == 200
