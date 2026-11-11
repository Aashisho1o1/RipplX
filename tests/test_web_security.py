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

from finwatch.db import Company, Repo, connect  # noqa: E402
from finwatch.demo import build_demo_db  # noqa: E402
from finwatch.web.app import REQUEST_BODY_LIMIT_BYTES, create_app
from finwatch.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from finwatch.web.jobs import JobItem, JobRegistry

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
    assert (
        client.get("/api/bootstrap", headers={"Authorization": f"Bearer {AUTH_SECRET}"}).status_code
        == 401
    )

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
    assert (
        no_origin.post("/api/auth/request-code", json={"email": "other@example.com"}).status_code
        == 403
    )


def test_remote_host_allowlist_applies_before_api_use(tmp_path, monkeypatch):
    app, _sender = _remote_app(tmp_path, monkeypatch)
    response = TestClient(app, base_url="https://attacker.example").get("/healthz")
    assert response.status_code == 400


def test_remote_demo_uses_isolated_bundled_sample_data(tmp_path, monkeypatch):
    app, sender = _remote_app(tmp_path, monkeypatch)
    client, _verified = _login(app, sender)
    response = client.get("/api/brief?demo=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sample_data"] is True
    assert payload["tracked_tickers"] == ["AAPL", "DPLS", "MSFT", "TWKS"]
    assert len(payload["filings"]) == 4
    assert sum(len(filing["findings"]) for filing in payload["filings"]) == 5
    assert payload["filings"][0]["findings"][0]["evidence"][0]["edgar_url"].startswith(
        "https://www.sec.gov/"
    )

    connection = connect(app.state.db_path)
    try:
        repo = Repo(connection)
        user = repo.get_user_by_email("person@example.com")
        assert user is not None
        repo.upsert_company(
            Company(
                cik="0000009999",
                ticker="QQQZ",
                name="Private watchlist fixture",
                added_at="2026-07-20T00:00:00Z",
            )
        )
        repo.track_company("0000009999", user_id=user.id, at="2026-07-20T00:00:00Z")
    finally:
        connection.close()

    assert client.get("/api/brief?demo=true").json()["tracked_tickers"] == [
        "AAPL",
        "DPLS",
        "MSFT",
        "TWKS",
    ]
    real_payload = client.get("/api/brief").json()
    assert real_payload["sample_data"] is False
    assert real_payload["tracked_tickers"] == ["QQQZ"]


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get(CSRF_COOKIE_NAME)}


def test_public_users_have_private_watchlists_preferences_filings_and_jobs(
    tmp_path, monkeypatch, offline_ticker_index
):
    db_path = tmp_path / "db.sqlite"
    build_demo_db(str(db_path)).close()
    app, sender = _remote_app(tmp_path, monkeypatch, db_path=db_path)
    alice, _ = _login(app, sender, "alice@example.com")
    bob, _ = _login(app, sender, "bob@example.com")

    added = alice.post("/api/companies", json={"ticker": "MSFT"}, headers=_csrf(alice))
    assert added.status_code == 201
    assert [row["ticker"] for row in alice.get("/api/companies").json()["companies"]] == ["MSFT"]
    assert bob.get("/api/companies").json()["companies"] == []
    assert alice.get("/api/brief").json()["tracked_tickers"] == ["MSFT"]
    assert bob.get("/api/brief").json()["tracked_tickers"] == []

    accession = "0000950170-24-048288"
    assert alice.get(f"/api/filings/{accession}").status_code == 200
    assert bob.get(f"/api/filings/{accession}").status_code == 404
    assert alice.get("/api/companies/MSFT/metrics").status_code == 200
    assert bob.get("/api/companies/MSFT/metrics").status_code == 404
    assert alice.get("/api/companies/MSFT/research").status_code == 200
    assert alice.get("/api/companies/MSFT/profile").status_code == 200
    for private_path in ("research", "profile", "risks", "peers"):
        assert bob.get(f"/api/companies/MSFT/{private_path}").status_code == 404
    assert bob.get("/api/alerts").json() == {"events": []}
    for endpoint in ("sync", "analyze"):
        rejected = bob.post(
            f"/api/jobs/{endpoint}",
            json={"ticker": "MSFT"},
            headers=_csrf(bob),
        )
        assert rejected.status_code == 404
        assert rejected.json()["error"]["code"] == "company_not_found"

    assert (
        alice.put("/api/settings", json={"period": "30d"}, headers=_csrf(alice)).status_code == 200
    )
    assert alice.get("/api/bootstrap").json()["period"] == "30d"
    assert bob.get("/api/bootstrap").json()["period"] == "90d"

    connection = connect(db_path)
    try:
        repo = Repo(connection)
        alice_id = repo.get_user_by_email("alice@example.com").id
    finally:
        connection.close()
    started = app.state.jobs.start("sync", lambda _job_id, _registry: False, owner_id=alice_id)
    for _ in range(100):
        if app.state.jobs.get(started.id, owner_id=alice_id).state == "completed":
            break
        time.sleep(0.005)
    assert alice.get(f"/api/jobs/{started.id}").status_code == 200
    assert bob.get(f"/api/jobs/{started.id}").status_code == 404


def test_operator_environment_key_enables_hosted_analysis_without_exposing_it(
    tmp_path, monkeypatch
):
    """The operator may serve every hosted participant from one server-side key.

    Onboarding must not demand a provider key from each person. The key itself stays in
    the process: it is never echoed to a browser, never written to SQLite, and never
    reported as anything richer than the configured/not-configured boolean.
    """
    operator_key = "operator-key-must-not-be-exposed"
    app, sender = _remote_app(tmp_path, monkeypatch)
    monkeypatch.setenv("FINWATCH_MODEL", "z-ai/glm-5.2")
    monkeypatch.setenv("ZAI_API_KEY", operator_key)
    alice, _ = _login(app, sender, "alice@example.com")

    bootstrap = alice.get("/api/bootstrap")
    payload = bootstrap.json()
    # A participant who supplied no key of their own can still analyze.
    assert payload["api_key_configured"] is True
    assert payload["analysis_configured"] is True
    assert payload["provider"] == "z.ai"
    # ...but the operator's credential never crosses the API boundary, and the source
    # of the key is not disclosed either.
    assert operator_key not in bootstrap.text
    assert "api_key_source" not in payload
    assert operator_key.encode() not in (tmp_path / "db.sqlite").read_bytes()
    assert (
        alice.put(
            "/api/settings/provider-key",
            json={"api_key": "user-key"},
            headers=_csrf(alice),
        ).status_code
        == 404
    )

    principal = app.state.session_codec.load(alice.cookies.get(SESSION_COOKIE_NAME))
    connection = connect(str(tmp_path / "db.sqlite"))
    try:
        repo = Repo(connection)
        repo.upsert_company(
            Company(cik="0000789019", ticker="MSFT", name="Microsoft", added_at="now")
        )
        repo.track_company("0000789019", user_id=principal.user_id, at="now")
    finally:
        connection.close()

    assert (
        alice.post(
            "/api/companies/MSFT/questions",
            json={"question": "What changed?"},
            headers=_csrf(alice),
        ).status_code
        == 404
    )


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
            "reason": None,
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
    app = web_app.create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    client = TestClient(app)

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _index: client.get("/api/bootstrap").status_code, range(8)))

    assert statuses == [200] * 8
    assert calls == {"init": 1, "connect": 8}


def test_public_sample_is_readable_without_an_account_and_leaks_no_operator_state(
    tmp_path, monkeypatch
):
    """The bundled sample must be reachable by an anonymous visitor.

    Bootstrap runs before any route renders, so while it required a session a link to
    the sample landed a signed-out visitor on the sign-in form. These routes are a
    GET-only prefix that hardcodes the demo database and the reserved local-user scope,
    rather than a `?demo=` flag on the private routes — a flag would leave
    `/api/brief?demo=false` reachable and scoped to whatever principal was injected.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "operator-key-must-not-be-implied")
    app, _sender = _remote_app(tmp_path, monkeypatch)
    anon = TestClient(app, base_url="https://alpha.example")

    boot = anon.get("/api/public/sample/bootstrap")
    assert boot.status_code == 200
    payload = boot.json()
    # An anonymous caller has no session, and the operator's configuration is not
    # public: never report a configured model/provider/key to one.
    assert payload["account_email"] is None
    assert payload["api_key_configured"] is False
    assert payload["analysis_configured"] is False
    assert payload["model"] == "" and payload["provider"] is None
    assert payload["setup_required"] is False

    brief = anon.get("/api/public/sample/brief")
    assert brief.status_code == 200 and brief.json()["sample_data"] is True
    companies = anon.get("/api/public/sample/companies")
    assert companies.status_code == 200
    assert {row["ticker"] for row in companies.json()["companies"]} == {
        "AAPL",
        "DPLS",
        "MSFT",
        "TWKS",
    }
    assert anon.get("/api/public/sample/companies/MSFT/research").status_code == 200
    alerts = anon.get("/api/public/sample/alerts")
    assert alerts.status_code == 200 and isinstance(alerts.json()["events"], list)
    accession = brief.json()["filings"][0]["accession"]
    assert anon.get(f"/api/public/sample/filings/{accession}").status_code == 200
    # The certificate is the artifact that best demonstrates the gate; it must be
    # reachable in the sample rather than 404ing behind ownership.
    assert anon.get(f"/api/public/sample/filings/{accession}/certificate").status_code == 200


def test_public_sample_prefix_never_widens_private_access(tmp_path, monkeypatch):
    app, _sender = _remote_app(tmp_path, monkeypatch)
    anon = TestClient(app, base_url="https://alpha.example")

    # Private routes stay private, with or without a demo flag.
    for path in ("/api/bootstrap", "/api/brief", "/api/brief?demo=true", "/api/companies"):
        assert anon.get(path).status_code == 401, path

    # The bypass is GET-only: no mutation may ride the public prefix.
    assert anon.post("/api/public/sample/brief").status_code in {403, 405}
    # And it grants nothing outside the sample namespace.
    assert anon.get("/api/public/sample/../bootstrap").status_code in {401, 404}


def test_hosted_account_has_a_bounded_hourly_model_job_allowance(tmp_path, monkeypatch):
    """Public signup must not be able to drain the operator's provider budget."""
    from finwatch.web.jobs import MODEL_JOBS_PER_OWNER_PER_HOUR

    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")
    app, sender = _remote_app(tmp_path, monkeypatch)
    alice, _ = _login(app, sender, "alice@example.com")
    bob, _ = _login(app, sender, "bob@example.com")
    started = []

    def fake_start(kind, work, *, owner_id):
        started.append(owner_id)
        return {
            "id": "0" * 32, "kind": kind, "state": "queued",
            "created_at": "now", "items": [], "error": None,
        }

    app.state.jobs.start = fake_start
    for _ in range(MODEL_JOBS_PER_OWNER_PER_HOUR):
        assert alice.post("/api/jobs/analyze", json={}, headers=_csrf(alice)).status_code == 202

    refused = alice.post("/api/jobs/analyze", json={}, headers=_csrf(alice))
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "job_rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 3600
    assert len(started) == MODEL_JOBS_PER_OWNER_PER_HOUR
    # The allowance is per account: one user's spending never locks out another.
    assert bob.post("/api/jobs/analyze", json={}, headers=_csrf(bob)).status_code == 202


def test_model_job_allowance_is_not_spent_by_a_refused_start(tmp_path, monkeypatch):
    from finwatch.web.jobs import JobConflictError

    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")
    app, sender = _remote_app(tmp_path, monkeypatch)
    client, _ = _login(app, sender)

    def busy(kind, work, *, owner_id):
        raise JobConflictError("Another background job is already running.")

    app.state.jobs.start = busy
    for _ in range(3):
        assert client.post("/api/jobs/analyze", json={}, headers=_csrf(client)).status_code == 409
    assert app.state.model_job_limiter._starts == {}


def test_job_rate_limiter_window_slides_and_release_returns_a_start():
    from finwatch.web.jobs import JobRateLimiter

    now = [0.0]
    limiter = JobRateLimiter(2, window_seconds=60, clock=lambda: now[0])
    assert limiter.reserve("u") is None
    now[0] = 30.0
    assert limiter.reserve("u") is None
    assert limiter.reserve("u") == 30
    assert limiter.reserve("other") is None
    limiter.release("u")
    assert limiter.reserve("u") is None
    now[0] = 60.0
    assert limiter.reserve("u") is None  # the first start left the window
    assert limiter.reserve("u") == 30


def test_job_rate_limiter_admission_is_atomic_under_concurrency():
    """Check-then-record let two concurrent requests both take the last start."""
    import threading

    from finwatch.web.jobs import JobRateLimiter

    limiter = JobRateLimiter(1)
    barrier = threading.Barrier(16)
    admitted = []

    def attempt():
        barrier.wait()
        admitted.append(limiter.reserve("u") is None)

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert admitted.count(True) == 1


def test_analysis_with_nothing_to_analyze_returns_its_allowance(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")
    app, sender = _remote_app(tmp_path, monkeypatch)
    client, _ = _login(app, sender)

    started = client.post("/api/jobs/analyze", json={}, headers=_csrf(client))
    assert started.status_code == 202
    job = started.json()
    for _ in range(400):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["state"] not in {"queued", "running"}:
            break
        time.sleep(0.005)

    assert job["state"] == "completed"
    assert [item["reason"] for item in job["items"]] == ["no_filings_synced"]
    assert app.state.model_job_limiter._starts == {}


def test_public_sample_metrics_default_and_validate_the_date(tmp_path):
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    client = TestClient(app)

    assert client.get("/api/public/sample/companies/MSFT/metrics").status_code == 200
    dated = client.get("/api/public/sample/companies/MSFT/metrics?as_of=2024-08-05")
    assert dated.status_code == 200
    assert client.get("/api/public/sample/companies/MSFT/metrics?as_of=zzz").status_code == 422


def test_asgi_server_log_withholds_unhandled_exception_text(tmp_path, monkeypatch, caplog):
    """Starlette re-raises after the generic 500 and uvicorn logs the traceback."""
    import logging
    import threading
    import time
    import urllib.error
    import urllib.request

    import uvicorn

    from finwatch.presentation import PresentationService

    sentinel = "sk-live-SENTINEL-in-unhandled-exception"

    def explode(_self):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(PresentationService, "companies", explode)
    app = create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    with caplog.at_level(logging.ERROR):
        thread.start()
        try:
            for _ in range(400):
                if server.started:
                    break
                time.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            with pytest.raises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/companies", timeout=5)
            body = raised.value.read().decode()
        finally:
            server.should_exit = True
            thread.join(timeout=5)

    assert raised.value.code == 500
    assert sentinel not in body
    server_records = [r for r in caplog.records if r.name == "uvicorn.error"]
    assert any("RuntimeError (message withheld)" in r.getMessage() for r in server_records)
    assert sentinel not in caplog.text
    assert all(r.exc_info is None for r in server_records)


@pytest.mark.parametrize(
    ("model", "skeptic"),
    [
        ("anthropic/claude", ""),
        ("openai/test", "anthropic/claude"),
        ("openai/test", "openrouter/other"),
        ("", "openai/test"),
    ],
)
def test_unusable_model_configuration_fails_at_startup(tmp_path, monkeypatch, model, skeptic):
    monkeypatch.setenv("FINWATCH_MODEL", model)
    monkeypatch.setenv("FINWATCH_SKEPTIC_MODEL", skeptic)
    with pytest.raises(RuntimeError, match="FINWATCH_(SKEPTIC_)?MODEL"):
        create_app(db_path=str(tmp_path / "db.sqlite"), web_dist=tmp_path / "missing")


def test_startup_fails_research_runs_orphaned_by_a_previous_process(tmp_path):
    import sqlite3

    db_path = tmp_path / "db.sqlite"
    build_demo_db(str(db_path)).close()
    conn = sqlite3.connect(db_path)
    cik = conn.execute("SELECT cik FROM companies LIMIT 1").fetchone()[0]
    for run_id, status in (("a" * 32, "queued"), ("b" * 32, "running"), ("c" * 32, "failed")):
        conn.execute(
            """INSERT INTO research_runs (id, user_id, cik, status, input_hash, created_at)
               VALUES (?, 'local', ?, ?, ?, '2026-08-03T00:00:00+00:00')""",
            (run_id, cik, status, run_id * 2),
        )
    conn.commit()
    conn.close()

    create_app(db_path=str(db_path), web_dist=tmp_path / "missing")

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute("SELECT id, status FROM research_runs").fetchall())
    conn.close()
    assert set(rows.values()) == {"failed"}


def test_job_failure_is_logged_by_type_without_exception_text(caplog):
    import logging
    import time

    from finwatch.web.jobs import JobRegistry

    registry = JobRegistry()
    sentinel = "sk-live-SENTINEL-in-job-exception"

    def work(_job_id, _registry):
        raise RuntimeError(sentinel)

    with caplog.at_level(logging.ERROR, logger="finwatch.jobs"):
        job = registry.start("analysis", work)
        for _ in range(200):
            if registry.get(job.id).state == "failed":
                break
            time.sleep(0.005)
    assert registry.get(job.id).state == "failed"
    assert "RuntimeError" in caplog.text
    assert sentinel not in caplog.text


def test_every_emitted_analytics_event_is_allowlisted():
    """A non-allowlisted event raised inside the request and turned it into a 500."""
    import re
    from pathlib import Path

    import finwatch.web.app as web_app
    from finwatch.product.providers import _ANALYTICS_EVENTS

    emitted = set(
        re.findall(r'capture\(\s*principal,\s*"([a-z_]+)"', Path(web_app.__file__).read_text())
    )
    assert emitted and emitted <= _ANALYTICS_EVENTS


def _billing_app(tmp_path, monkeypatch):
    from finwatch.product.providers import StripeClient

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_operator")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_founding")
    calls = {"customers": [], "checkouts": []}

    def create_customer(_self, *, email, user_id):
        calls["customers"].append((email, user_id))
        return f"cus_{len(calls['customers'])}"

    def checkout(_self, **kwargs):
        calls["checkouts"].append(kwargs)
        return "https://checkout.stripe.com/c/pay_test"

    monkeypatch.setattr(StripeClient, "create_customer", create_customer)
    monkeypatch.setattr(StripeClient, "checkout", checkout)
    app, sender = _remote_app(tmp_path, monkeypatch)
    return app, sender, calls


def test_every_checkout_for_an_account_shares_one_stripe_customer(tmp_path, monkeypatch):
    """A second click before the first webhook landed used to create a second customer."""
    app, sender, calls = _billing_app(tmp_path, monkeypatch)
    client, _ = _login(app, sender)

    for _ in range(3):
        assert client.post("/api/billing/checkout", headers=_csrf(client)).status_code == 200

    assert calls["customers"] == [("person@example.com", calls["customers"][0][1])]
    assert {kwargs["customer_id"] for kwargs in calls["checkouts"]} == {"cus_1"}
    # Linking the customer does not grant the plan.
    assert client.get("/api/bootstrap").json()["billing_status"] == "free"


def test_a_checkout_on_a_second_customer_never_moves_the_account(tmp_path, monkeypatch):
    import hashlib
    import hmac
    import json
    import time

    from finwatch.product.providers import StripeClient

    app, sender, _calls = _billing_app(tmp_path, monkeypatch)
    client, _ = _login(app, sender)
    client.post("/api/billing/checkout", headers=_csrf(client))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(
        StripeClient,
        "customer_subscriptions",
        lambda _self, customer: [
            {"id": "sub_x", "customer": customer, "status": "active", "created": 1}
        ],
    )
    user_id = _calls["customers"][0][1]
    body = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "mode": "subscription", "client_reference_id": user_id,
            "customer": "cus_other", "subscription": "sub_x",
        }},
    }).encode()
    timestamp = int(time.time())
    signature = hmac.new(b"whsec_test", f"{timestamp}.".encode() + body, hashlib.sha256)
    webhook = TestClient(app, base_url="https://alpha.example")

    response = webhook.post(
        "/api/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": f"t={timestamp},v1={signature.hexdigest()}"},
    )

    assert response.status_code == 204
    assert client.get("/api/bootstrap").json()["billing_status"] == "free"


def test_a_stuck_billing_delivery_answers_503_instead_of_waiting(tmp_path, monkeypatch):
    import threading

    import finwatch.web.app as web_app

    app, sender, _calls = _billing_app(tmp_path, monkeypatch)
    client, _ = _login(app, sender)
    monkeypatch.setattr(web_app, "_BILLING_LOCK_TIMEOUT_SECONDS", 0.05)
    app.state.billing_lock = threading.Lock()
    app.state.billing_lock.acquire()

    response = client.post("/api/billing/checkout", headers=_csrf(client))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "billing_busy"
