"""Hosted-alpha authentication and host-boundary regression tests."""

from __future__ import annotations

import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.web.app import REQUEST_BODY_LIMIT_BYTES, create_app
from finwatch.web.jobs import JobItem, JobRegistry

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
            create_app(remote=True, auth_token=TOKEN, allowed_hosts=bad_hosts, **kwargs)


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


def test_demo_parameter_is_ignored_in_remote_mode(tmp_path):
    # LOW-6: the bundled demo dataset is a local-only convenience; ?demo=true must not
    # serve sample data on a hosted deployment.
    app = create_app(
        db_path=str(tmp_path / "db.sqlite"),
        web_dist=tmp_path / "missing",
        remote=True,
        auth_token=TOKEN,
        allowed_hosts=["alpha.example"],
    )
    client = TestClient(app, base_url="https://alpha.example")
    response = client.get(
        "/api/brief?demo=true", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["sample_data"] is False


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
            "message": "Finding important changes could not be completed.",
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
