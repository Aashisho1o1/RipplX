"""Hosted-alpha authentication and host-boundary regression tests."""

from __future__ import annotations

import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.db import Repo, connect  # noqa: E402
from finwatch.demo import build_demo_db  # noqa: E402
from finwatch.web.app import REQUEST_BODY_LIMIT_BYTES, create_app
from finwatch.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from finwatch.web.jobs import JobItem, JobRegistry
from finwatch.web.runtime import RuntimeSecrets

AUTH_SECRET = "a" * 32


def test_docker_build_context_excludes_environment_secret_files():
    rules = (Path(__file__).parents[1] / ".dockerignore").read_text(encoding="utf-8")
    assert "\n.env\n" in f"\n{rules}"
    assert ".env.*" in rules
    assert "!.env.example" in rules


class RecordingEmailSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, recipient: str, code: str) -> None:
        self.sent.append((recipient, code))


def _remote_app(tmp_path, monkeypatch, *, db_path=None):
    monkeypatch.setenv("SEC_USER_AGENT", "RipplX Operator operator@example.com")
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")
    sender = RecordingEmailSender()
    app = create_app(
        db_path=str(db_path or tmp_path / "db.sqlite"),
        web_dist=tmp_path / "missing",
        remote=True,
        auth_secret=AUTH_SECRET,
        allowed_hosts=["alpha.example"],
        email_sender=sender,
    )
    return app, sender


def _login(app, sender, email="person@example.com"):
    client = TestClient(
        app,
        base_url="https://alpha.example",
        headers={"Origin": "https://alpha.example"},
    )
    requested = client.post("/api/auth/request-code", json={"email": email})
    assert requested.status_code == 202
    assert requested.json()["expires_in"] <= 600
    verified = client.post(
        "/api/auth/verify-code",
        json={
            "challenge_id": requested.json()["challenge_id"],
            "code": sender.sent[-1][1],
        },
    )
    assert verified.status_code == 204
    return client, verified


def test_remote_app_refuses_missing_or_weak_security_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "RipplX Operator operator@example.com")
    kwargs = {"db_path": str(tmp_path / "db.sqlite"), "web_dist": tmp_path / "missing"}
    sender = RecordingEmailSender()
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        create_app(
            remote=True,
            allowed_hosts=["alpha.example"],
            email_sender=sender,
            **kwargs,
        )
    with pytest.raises(RuntimeError, match="at least 32"):
        create_app(
            remote=True,
            auth_secret="too-short",
            allowed_hosts=["alpha.example"],
            email_sender=sender,
            **kwargs,
        )
    monkeypatch.delenv("SEC_USER_AGENT")
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        create_app(
            remote=True,
            auth_secret=AUTH_SECRET,
            allowed_hosts=["alpha.example"],
            email_sender=sender,
            **kwargs,
        )
    monkeypatch.setenv("SEC_USER_AGENT", "RipplX Operator operator@example.com")
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        create_app(
            remote=True,
            auth_secret=AUTH_SECRET,
            allowed_hosts=["alpha.example"],
            **kwargs,
        )
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        create_app(
            remote=True,
            auth_secret=AUTH_SECRET,
            allowed_hosts=[],
            email_sender=sender,
            **kwargs,
        )
    # A bare "*", a leading-"*." wildcard (which Starlette's TrustedHostMiddleware
    # treats as a real subdomain match), and leading-dot patterns must all be
    # rejected — the exact-host guarantee (AGENTS.md §12) forbids any wildcard.
    for bad_hosts in (
        ["*"],
        ["*.example.com"],
        [".example.com"],
        ["alpha.example", "*.evil.com"],
    ):
        with pytest.raises(RuntimeError, match="wildcards"):
            create_app(
                remote=True,
                auth_secret=AUTH_SECRET,
                allowed_hosts=bad_hosts,
                email_sender=sender,
                **kwargs,
            )


def test_remote_api_uses_persistent_cookie_session_and_logout(tmp_path, monkeypatch):
    app, sender = _remote_app(tmp_path, monkeypatch)
    client = TestClient(app, base_url="https://alpha.example")

    assert client.get("/healthz").json() == {"status": "ok"}
    missing = client.get("/api/bootstrap")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_required"
    assert client.get(
        "/api/bootstrap", headers={"Authorization": f"Bearer {AUTH_SECRET}"}
    ).status_code == 401

    client, verified = _login(app, sender)
    cookie_headers = verified.headers.get_list("set-cookie")
    assert any(
        SESSION_COOKIE_NAME in value
        and "HttpOnly" in value
        and "Secure" in value
        and "SameSite=lax" in value
        for value in cookie_headers
    )
    assert client.get("/api/bootstrap").json()["account_email"] == "person@example.com"
    assert client.get("/api/bootstrap").status_code == 200  # survives refresh

    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "bad"}).status_code == 403
    csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/bootstrap").status_code == 401


def test_bootstrap_restores_a_missing_csrf_cookie(tmp_path, monkeypatch):
    app, sender = _remote_app(tmp_path, monkeypatch)
    client, _ = _login(app, sender)
    client.cookies.delete(CSRF_COOKIE_NAME)

    assert client.get("/api/bootstrap").status_code == 200
    restored = client.cookies.get(CSRF_COOKIE_NAME)
    assert restored
    updated = client.put(
        "/api/settings",
        json={"period": "30d"},
        headers={"X-CSRF-Token": restored},
    )
    assert updated.status_code == 200


def test_remote_rejects_tampered_session_and_mutations_without_origin(tmp_path, monkeypatch):
    app, sender = _remote_app(tmp_path, monkeypatch)
    client, _ = _login(app, sender)
    client.cookies.set(SESSION_COOKIE_NAME, "tampered", domain="alpha.example", path="/")
    assert client.get("/api/bootstrap").status_code == 401

    no_origin = TestClient(app, base_url="https://alpha.example")
    assert no_origin.post(
        "/api/auth/request-code", json={"email": "other@example.com"}
    ).status_code == 403


def test_remote_host_allowlist_applies_before_api_use(tmp_path, monkeypatch):
    app, _sender = _remote_app(tmp_path, monkeypatch)
    response = TestClient(app, base_url="https://attacker.example").get("/healthz")
    assert response.status_code == 400


def test_demo_parameter_is_ignored_in_remote_mode(tmp_path, monkeypatch):
    # LOW-6: the bundled demo dataset is a local-only convenience; ?demo=true must not
    # serve sample data on a hosted deployment.
    app, sender = _remote_app(tmp_path, monkeypatch)
    client, _verified = _login(app, sender)
    response = client.get("/api/brief?demo=true")
    assert response.status_code == 200
    assert response.json()["sample_data"] is False


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get(CSRF_COOKIE_NAME)}


def test_public_users_have_private_watchlists_preferences_filings_and_jobs(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "db.sqlite"
    build_demo_db(str(db_path)).close()
    app, sender = _remote_app(tmp_path, monkeypatch, db_path=db_path)
    alice, _ = _login(app, sender, "alice@example.com")
    bob, _ = _login(app, sender, "bob@example.com")

    added = alice.post(
        "/api/companies", json={"ticker": "MSFT"}, headers=_csrf(alice)
    )
    assert added.status_code == 201
    assert [row["ticker"] for row in alice.get("/api/companies").json()["companies"]] == [
        "MSFT"
    ]
    assert bob.get("/api/companies").json()["companies"] == []
    assert alice.get("/api/brief").json()["tracked_tickers"] == ["MSFT"]
    assert bob.get("/api/brief").json()["tracked_tickers"] == []

    accession = "0000950170-24-048288"
    assert alice.get(f"/api/filings/{accession}").status_code == 200
    assert bob.get(f"/api/filings/{accession}").status_code == 404
    assert alice.get("/api/companies/MSFT/metrics").status_code == 200
    assert bob.get("/api/companies/MSFT/metrics").status_code == 404
    for endpoint in ("sync", "analyze"):
        rejected = bob.post(
            f"/api/jobs/{endpoint}",
            json={"ticker": "MSFT"},
            headers=_csrf(bob),
        )
        assert rejected.status_code == 404
        assert rejected.json()["error"]["code"] == "company_not_found"

    assert alice.put(
        "/api/settings", json={"period": "30d"}, headers=_csrf(alice)
    ).status_code == 200
    assert alice.get("/api/bootstrap").json()["period"] == "30d"
    assert bob.get("/api/bootstrap").json()["period"] == "90d"

    connection = connect(db_path)
    try:
        repo = Repo(connection)
        alice_id = repo.get_user_by_email("alice@example.com").id
    finally:
        connection.close()
    started = app.state.jobs.start(
        "sync", lambda _job_id, _registry: False, owner_id=alice_id
    )
    for _ in range(100):
        if app.state.jobs.get(started.id, owner_id=alice_id).state == "completed":
            break
        time.sleep(0.005)
    assert alice.get(f"/api/jobs/{started.id}").status_code == 200
    assert bob.get(f"/api/jobs/{started.id}").status_code == 404


def test_provider_keys_are_session_isolated_and_environment_key_is_ignored_remotely(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "operator-key-must-not-be-shared")
    app, sender = _remote_app(tmp_path, monkeypatch)
    alice, _ = _login(app, sender, "alice@example.com")
    bob, _ = _login(app, sender, "bob@example.com")

    initial = alice.get("/api/bootstrap").json()
    assert initial["api_key_configured"] is False
    assert "api_key_source" not in initial
    sentinel = "alice-session-provider-secret"
    response = alice.put(
        "/api/settings/provider-key",
        json={"api_key": sentinel},
        headers=_csrf(alice),
    )
    assert response.status_code == 204
    assert sentinel not in response.text
    assert alice.get("/api/bootstrap").json()["api_key_configured"] is True
    assert bob.get("/api/bootstrap").json()["api_key_configured"] is False

    alice_session = app.state.session_codec.load(
        alice.cookies.get(SESSION_COOKIE_NAME)
    )
    bob_session = app.state.session_codec.load(bob.cookies.get(SESSION_COOKIE_NAME))
    assert app.state.secrets.api_key(alice_session.session_id) == sentinel
    assert app.state.secrets.api_key(bob_session.session_id) is None
    assert sentinel.encode() not in (tmp_path / "db.sqlite").read_bytes()

    assert alice.post(
        "/api/auth/logout", headers=_csrf(alice)
    ).status_code == 204
    assert app.state.secrets.api_key(alice_session.session_id) is None


def test_provider_key_is_pruned_when_its_session_expires():
    now = [1_000.0]
    secrets = RuntimeSecrets(clock=lambda: now[0])
    secrets.set_api_key("session", "secret", expires_at=1_010)
    assert secrets.api_key("session") == "secret"

    now[0] = 1_010.0
    assert secrets.api_key("session") is None


def test_local_api_remains_auth_free(tmp_path):
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    assert TestClient(app).get("/api/bootstrap").status_code == 200


def test_unhandled_api_error_is_generic_json_without_exception_text(tmp_path, monkeypatch):
    from finwatch.presentation import PresentationService

    secret = "sk-live-must-never-reach-response"

    def fail(_self, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(PresentationService, "brief", fail)
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    response = TestClient(app, raise_server_exceptions=False).get("/api/brief")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "The request could not be completed.",
        }
    }
    assert secret not in response.text


def test_job_api_never_returns_exception_or_progress_diagnostics(tmp_path):
    sentinel = "sk-live-SENTINEL-provider-secret"
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    work_started = threading.Event()

    def leaking_work(job_id, registry):
        registry.upsert_item(
            job_id,
            JobItem(
                key="MSFT:extract",
                state="failed",
                stage="extract",
                message=f"provider rejected {sentinel}",
                verdict=sentinel,
                diagnostics={"provider_response": sentinel, "nested": {"token": sentinel}},
            ),
        )
        work_started.set()
        raise RuntimeError(f"LLM request failed with api_key={sentinel}")

    started = app.state.jobs.start("analysis", leaking_work)
    assert work_started.wait(timeout=1)
    client = TestClient(app)
    for _ in range(100):
        response = client.get(f"/api/jobs/{started.id}")
        if response.json()["state"] == "failed":
            break
        time.sleep(0.005)

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert sentinel not in response.text
    assert response.json()["error"] == "Analysis could not be completed."
    assert response.json()["items"] == [
        {
            "key": "MSFT:extract",
            "state": "failed",
            "message": "Researching important changes could not be completed.",
            "verdict": None,
            "stage": "extract",
            "diagnostics": {},
        }
    ]


def test_job_registry_bounds_completed_history():
    registry = JobRegistry(max_jobs=2)
    job_ids = []
    for _ in range(3):
        started = registry.start("sync", lambda _job_id, _registry: False)
        job_ids.append(started.id)
        for _attempt in range(100):
            if registry.get(started.id).state == "completed":
                break
            time.sleep(0.005)

    assert registry.get(job_ids[0]) is None
    assert registry.get(job_ids[1]) is not None
    assert registry.get(job_ids[2]) is not None


def test_request_body_limit_rejects_declared_oversize_with_json_contract(tmp_path):
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    response = TestClient(app).put(
        "/api/settings",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(REQUEST_BODY_LIMIT_BYTES + 1),
            "Origin": "http://testserver",
        },
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "request_too_large",
            "message": "Request body exceeds the 1 MiB limit.",
        }
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_request_body_limit_counts_streamed_chunks_without_content_length(tmp_path):
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")

    def chunks():
        yield b'{"api_key":"'
        chunk = b"x" * (REQUEST_BODY_LIMIT_BYTES // 4)
        for _ in range(5):
            yield chunk
        yield b'"}'

    response = TestClient(app).put(
        "/api/settings",
        content=chunks(),
        headers={"Content-Type": "application/json", "Origin": "http://testserver"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_production_schema_initializes_once_before_concurrent_requests(tmp_path, monkeypatch):
    web_app = importlib.import_module("finwatch.web.app")
    real_init_db = web_app.init_db
    real_connect = web_app.connect
    calls = {"init": 0, "connect": 0}
    lock = threading.Lock()

    def counted_init(path):
        with lock:
            calls["init"] += 1
        return real_init_db(path)

    def counted_connect(path):
        with lock:
            calls["connect"] += 1
        return real_connect(path)

    monkeypatch.setattr(web_app, "init_db", counted_init)
    monkeypatch.setattr(web_app, "connect", counted_connect)
    app = web_app.create_app(
        db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing"
    )
    client = TestClient(app)

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _index: client.get("/api/bootstrap").status_code, range(8)))

    assert statuses == [200] * 8
    assert calls == {"init": 1, "connect": 8}
