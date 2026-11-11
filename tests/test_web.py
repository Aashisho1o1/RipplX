import inspect
import json
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from finwatch.db import Company, Repo, init_db
from finwatch.demo import (
    PUBLIC_SHOWCASE_USER_ID,
    build_demo_db,
    publish_public_showcase,
)
from finwatch.ingest import EdgarClient, IngestService
from finwatch.web.app import CompanyCreate, JobRequest, _compute_synced_metrics, create_app

LOCAL_BROWSER_HEADERS = {"Origin": "http://testserver"}


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "finwatch.db"
    app = create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist")
    return TestClient(app, headers=LOCAL_BROWSER_HEADERS), db_path


def test_web_job_request_accepts_optional_ticker_and_supported_filing_type():
    assert JobRequest().ticker is None
    assert JobRequest(ticker="MSFT").ticker == "MSFT"
    assert JobRequest(form_type="10-Q").form_type == "10-Q"
    for legacy in (
        {"limit": 2},
        {"form": "10-Q"},
        {"form_type": "20-F"},
        {"accession": "a-1"},
        {"mode": "parse"},
    ):
        with pytest.raises(ValueError):
            JobRequest(**legacy)


def test_holding_create_is_ticker_only_and_legacy_rows_do_not_leak_private_fields(tmp_path):
    assert CompanyCreate(ticker="BRK-B").ticker == "BRK-B"
    for legacy in (
        {"ticker": "MSFT", "shares": 10},
        {"ticker": "MSFT", "cost_basis": 100},
        {"ticker": "MSFT", "target_weight_pct": 5},
        {"ticker": "MSFT", "thesis": "private"},
        {"ticker": "MSFT", "owned": False},
    ):
        with pytest.raises(ValueError):
            CompanyCreate(**legacy)

    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    client = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"),
        headers=LOCAL_BROWSER_HEADERS,
    )
    rows = client.get("/api/companies").json()["companies"]
    assert rows
    assert {"shares", "cost_basis", "target_weight_pct", "horizon", "thesis"}.isdisjoint(rows[0])
    assert client.patch("/api/companies/MSFT", json={"shares": 1}).status_code == 404


def test_demo_company_index_uses_the_same_sample_scope_as_company_research(tmp_path):
    client, _ = _client(tmp_path)

    rows = client.get("/api/companies?demo=true").json()["companies"]

    assert {row["ticker"] for row in rows} == {"AAPL", "DPLS", "MSFT", "TWKS"}


def test_before_you_buy_sync_populates_filings_and_verified_numbers(
    tmp_path, monkeypatch, offline_ticker_index
):
    fixtures = Path(__file__).parent / "fixtures"
    submissions = json.loads(
        (fixtures / "submissions_CIK0000320193.json").read_text(encoding="utf-8")
    )
    companyfacts = json.loads(
        (
            Path(__file__).parents[1]
            / "src"
            / "finwatch"
            / "demo"
            / "data"
            / "companyfacts_AAPL.json"
        ).read_text(encoding="utf-8")
    )
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    # Pin every clock this flow reads. The submissions fixture ends 2024-11-01 and the
    # companyfacts fixture in 2026, so the eight-quarter ingest window and the metric
    # staleness limits would otherwise both slide past the fixtures as real time passes.
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 3)

    monkeypatch.setattr(IngestService, "_as_of_date", lambda _self: FixedDate.today())
    monkeypatch.setattr(
        EdgarClient,
        "submissions",
        lambda _self, _cik, force_refresh=True: submissions,
    )
    monkeypatch.setattr(
        EdgarClient,
        "companyfacts",
        lambda _self, _cik, force_refresh=True: companyfacts,
    )
    client, _ = _client(tmp_path)
    # After app creation, so route signatures were built with the real date type.
    monkeypatch.setattr("finwatch.web.app.date", FixedDate)
    monkeypatch.setattr("finwatch.product.service.date", FixedDate)

    added = client.post("/api/companies", json={"ticker": "AAPL"})
    assert added.status_code == 201
    assert added.json()["newest_supported_filing"] is None
    assert added.json()["compressed_verified_read"] is None

    started = client.post("/api/jobs/sync", json={"ticker": "AAPL"})
    assert started.status_code == 202
    job_id = started.json()["id"]
    terminal = started.json()
    for _ in range(200):
        terminal = client.get(f"/api/jobs/{job_id}").json()
        if terminal["state"] not in {"queued", "running"}:
            break
        time.sleep(0.005)

    assert terminal["state"] == "completed"
    brief = client.get("/api/companies/AAPL/research")
    assert brief.status_code == 200
    assert brief.json()["recent_filings"]
    assert brief.json()["metrics"]["rows"]
    assert any(row["state"] == "computed" for row in brief.json()["metrics"]["rows"])


def test_public_showcase_falls_back_to_bundled_sec_fixtures(tmp_path):
    client, _ = _client(tmp_path)

    bootstrap = client.get("/api/public/sample/bootstrap")
    companies = client.get("/api/public/sample/companies")

    assert bootstrap.status_code == 200
    assert bootstrap.json()["showcase_source"] == "bundled_fixture"
    assert bootstrap.json()["showcase_updated_at"] is None
    assert {row["ticker"] for row in companies.json()["companies"]} == {
        "AAPL",
        "DPLS",
        "MSFT",
        "TWKS",
    }


def test_public_showcase_prefers_isolated_atomically_published_sec_cache(tmp_path):
    client, db_path = _client(tmp_path)
    connection = init_db(str(db_path))
    try:
        repo = Repo(connection)
        repo.upsert_company(
            Company(cik="0000000042", ticker="LIVE", name="Live Cache", added_at="t")
        )
        publish_public_showcase(
            repo,
            ["0000000042"],
            refreshed_at="2026-08-03T12:00:00+00:00",
        )
        assert repo.list_tracked_ciks() == []
        assert repo.list_tracked_ciks(PUBLIC_SHOWCASE_USER_ID) == ["0000000042"]
    finally:
        connection.close()

    bootstrap = client.get("/api/public/sample/bootstrap").json()
    public_rows = client.get("/api/public/sample/companies").json()["companies"]
    private_rows = client.get("/api/companies").json()["companies"]

    assert bootstrap["showcase_source"] == "sec_cache"
    assert bootstrap["showcase_updated_at"] == "2026-08-03T12:00:00+00:00"
    assert [row["ticker"] for row in public_rows] == ["LIVE"]
    assert private_rows == []


def test_failed_showcase_publication_keeps_the_previous_curated_set(tmp_path):
    _, db_path = _client(tmp_path)
    connection = init_db(str(db_path))
    try:
        repo = Repo(connection)
        repo.upsert_company(Company(cik="1", ticker="OLD", added_at="t"))
        publish_public_showcase(repo, ["1"], refreshed_at="old")

        with pytest.raises(ValueError, match="unknown showcase CIK"):
            publish_public_showcase(repo, ["2"], refreshed_at="new")

        assert repo.list_tracked_ciks(PUBLIC_SHOWCASE_USER_ID) == ["1"]
        assert repo.get_setting("web.public_showcase_refreshed_at") == "old"
    finally:
        connection.close()


def test_holding_create_fails_before_edgar_when_launch_cap_is_reached(tmp_path):
    client, db_path = _client(tmp_path)
    assert (
        client.put(
            "/api/settings",
            json={"sec_user_agent": "Test User test@example.com"},
        ).status_code
        == 200
    )
    conn = init_db(str(db_path))
    try:
        repo = Repo(conn)
        for index in range(25):
            cik = str(index + 1)
            ticker = f"T{index}"
            repo.upsert_company(Company(cik=cik, ticker=ticker, added_at="t"))
            repo.track_company(cik, at="t")
    finally:
        conn.close()

    response = client.post("/api/companies", json={"ticker": "NEW"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tracked_ticker_limit"


def test_analyze_endpoint_rejects_historical_replay_controls(tmp_path):
    client, _ = _client(tmp_path)
    for payload in (
        {"limit": 10},
        {"form": "10-Q"},
        {"form_type": "20-F"},
        {"accession": "a-1"},
        {"mode": "parse"},
    ):
        assert client.post("/api/jobs/analyze", json=payload).status_code == 422


def test_metrics_endpoint_rejects_malformed_as_of_date(tmp_path):
    client, _ = _client(tmp_path)
    response = client.get("/api/companies/MSFT/metrics?as_of=not-a-date")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_get_and_delete_path_params_reject_malformed_input(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/filings/not-an-accession").status_code == 422
    assert client.get("/api/companies/BAD!TICKER/metrics").status_code == 422
    assert client.delete("/api/companies/BAD!TICKER").status_code == 422
    assert client.get("/api/jobs/not-a-valid-job-id").status_code == 422
    # a well-formed but unknown accession passes validation and 404s at lookup
    assert client.get("/api/filings/0000000001-24-000001").status_code == 404


def test_company_research_assembles_risks_profile_and_metrics(tmp_path):
    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    client = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"),
        headers=LOCAL_BROWSER_HEADERS,
    )
    response = client.get("/api/companies/MSFT/research")
    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "MSFT"
    assert len(body["risks"]) == 8
    assert {row["status"] for row in body["risks"]} <= {
        "stable",
        "watch",
        "elevated",
        "unavailable",
    }
    assert body["metrics"]["rows"]
    assert body["thesis"] == {"items": []}
    assert body["profile"]["monitoring_enabled"] is True
    assert "questions" not in body
    assert "valuation" not in body
    assert body["impact"]["directional_pressure"] in {
        "upside",
        "downside",
        "mixed",
        "uncertain",
    }

    profile = client.put(
        "/api/companies/MSFT/profile",
        json={
            "monitoring_enabled": False,
            "notification_level": "weekly",
            "peer_tickers": ["AAPL"],
        },
    )
    assert profile.status_code == 200
    assert profile.json()["notification_level"] == "weekly"
    refreshed = client.get("/api/companies/MSFT/research").json()
    assert refreshed["profile"]["monitoring_enabled"] is False
    assert refreshed["manual_peer_tickers"] == ["AAPL"]

    draft = client.post("/api/companies/MSFT/thesis/draft")
    assert draft.status_code == 200
    assert all(row["status"] == "draft" for row in draft.json()["items"])

    assert client.post("/api/companies/MSFT/valuation", json={}).status_code == 404
    assert client.post("/api/companies/MSFT/questions", json={}).status_code == 404


def test_stripe_webhook_rejects_unsigned_payload_without_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    client, _ = _client(tmp_path)
    response = client.post(
        "/api/webhooks/stripe",
        content=b'{"type":"checkout.session.completed"}',
        headers={"Stripe-Signature": "t=1,v1=bad"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_webhook"


def test_bootstrap_setup_and_operator_key_are_safe(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-secret-value")
    client, db_path = _client(tmp_path)
    assert client.get("/api/bootstrap").json()["setup_required"] is True

    response = client.put(
        "/api/settings",
        json={"sec_user_agent": "Test User test@example.com"},
    )
    assert response.status_code == 200
    body = client.get("/api/bootstrap").json()
    assert body["setup_required"] is False
    assert body["api_key_configured"] is True
    assert body["model"] == "openai/test"
    assert body["billing_status"] == "free"
    assert "operator-secret-value" not in response.text

    conn = init_db(str(db_path))
    try:
        settings = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()
    assert "operator-secret-value" not in settings.values()
    assert client.put("/api/settings", json={"model_extract": "anthropic/other"}).status_code == 422


def test_only_openai_environment_credentials_configure_production(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-be-ignored")

    client, _ = _client(tmp_path)
    bootstrap = client.get("/api/bootstrap").json()
    assert bootstrap["model"] == "openai/test"
    assert bootstrap["api_key_configured"] is False


def test_restart_keeps_portfolio_results_and_operator_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")

    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    first = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"),
        headers=LOCAL_BROWSER_HEADERS,
    )
    assert (
        first.put(
            "/api/settings",
            json={"sec_user_agent": "Test User test@example.com"},
        ).status_code
        == 200
    )
    assert first.get("/api/bootstrap").json()["api_key_configured"] is True
    assert len(first.get("/api/companies").json()["companies"]) == 4

    restarted = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"),
        headers=LOCAL_BROWSER_HEADERS,
    )
    bootstrap = restarted.get("/api/bootstrap").json()
    assert bootstrap["sec_user_agent"] == "Test User test@example.com"
    assert bootstrap["model"] == "openai/test"
    assert bootstrap["api_key_configured"] is True
    assert len(restarted.get("/api/companies").json()["companies"]) == 4
    filing = restarted.get("/api/filings/0001683168-24-004848")
    assert filing.status_code == 200
    assert filing.json()["verification"] is not None
    pipeline = {stage["stage"]: stage["status"] for stage in filing.json()["pipeline"]}
    assert pipeline["parse"] == "completed"
    assert pipeline["verify"] == "completed"


def test_certificate_endpoint_returns_stable_attempt_linked_v2_artifact(tmp_path):
    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    client = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"),
        headers=LOCAL_BROWSER_HEADERS,
    )
    url = "/api/filings/0001683168-24-004848/certificate"

    first = client.get(url)
    second = client.get(url)

    assert first.status_code == 200
    assert first.content == second.content
    payload = first.json()
    assert payload["schema_version"] == "certificate.v2"
    assert payload["p1_analysis_id"] > 0
    assert payload["trace_analysis_id"] > 0
    assert len(payload["p1_output_sha256"]) == 64
    assert len(payload["certificate_sha256"]) == 64


def test_analysis_captures_operator_key_before_enqueue(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")
    app = create_app(db_path=str(tmp_path / "finwatch.db"), web_dist=tmp_path / "missing-dist")
    client = TestClient(app, headers=LOCAL_BROWSER_HEADERS)
    captured = {}

    def fake_start(kind, work, *, owner_id):
        captured.update(inspect.getclosurevars(work).nonlocals)
        captured["owner_id"] = owner_id
        return {
            "id": "0" * 32,
            "kind": kind,
            "state": "queued",
            "created_at": "now",
            "items": [],
            "error": None,
        }

    app.state.jobs.start = fake_start
    assert client.post("/api/jobs/analyze", json={}).status_code == 202

    assert captured["api_key"] == "operator-key"
    assert captured["owner_id"] == "local"


def test_deep_research_run_is_persisted_polled_and_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")
    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    app = create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist")
    client = TestClient(app, headers=LOCAL_BROWSER_HEADERS)
    starts = []

    def fake_start(kind, work, *, owner_id, job_id=None):
        starts.append((kind, owner_id, job_id, inspect.getclosurevars(work).nonlocals))
        return {
            "id": job_id,
            "kind": kind,
            "state": "queued",
            "created_at": "now",
            "items": [],
            "error": None,
        }

    app.state.jobs.start = fake_start
    first = client.post("/api/companies/MSFT/research-runs")
    second = client.post("/api/companies/MSFT/research-runs")

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["status"] == "queued"
    assert starts[0][0:3] == ("research", "local", first.json()["run_id"])
    assert starts[0][3]["api_key"] == "operator-key"
    assert len(starts) == 1
    polled = client.get(f"/api/research-runs/{first.json()['run_id']}")
    assert polled.status_code == 200
    assert polled.json() == first.json()


def test_jobs_reject_untracked_ticker_before_occupying_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")
    client, _ = _client(tmp_path)

    for endpoint in ("sync", "analyze"):
        response = client.post(f"/api/jobs/{endpoint}", json={"ticker": "MSFT"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "company_not_found"


def test_demo_contract_has_no_shadow_surface(tmp_path):
    client, _ = _client(tmp_path)
    response = client.get("/api/brief?demo=true&include_signals=true")
    assert response.status_code == 200
    body = response.json()
    assert body["sample_data"] is True
    assert body["filings"][0]["findings"][0]["evidence"][0]["edgar_url"].startswith("https://")
    assert "shadow_signals" not in body
    assert "signals" not in client.get("/api/bootstrap").json()


def test_brief_api_does_not_expose_historical_replay_controls(tmp_path):
    client, _ = _client(tmp_path)

    operation = client.get("/openapi.json").json()["paths"]["/api/brief"]["get"]
    query_names = {parameter["name"] for parameter in operation["parameters"]}

    assert query_names == {"demo"}


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

    client = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"),
        headers=LOCAL_BROWSER_HEADERS,
    )
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

        computed = _compute_synced_metrics(service, "0000789019", as_of="2024-08-05")

        assert computed > 0
        assert len(repo.list_computations("MSFT")) > before
    finally:
        conn.close()


def test_add_resolves_issuer_identity_instead_of_trusting_a_stale_ticker_row(
    tmp_path, offline_ticker_index
):
    """Registration must not infer the issuer from a stored ticker match.

    Tickers are recycled, so a ``companies`` row carrying the symbol proves nothing
    about who files under it today. The route used to short-circuit on that row and
    track its CIK without ever consulting the SEC index, silently attributing one
    issuer's filings to another — the worst failure mode this product has, and the one
    a bulk import would hit routinely. It must fail closed instead.
    """
    client, db_path = _client(tmp_path)
    assert (
        client.put(
            "/api/settings", json={"sec_user_agent": "Test User test@example.com"}
        ).status_code
        == 200
    )

    # MSFT is on file for a stale CIK; the current SEC index says 0000000789019.
    conn = init_db(str(db_path))
    try:
        Repo(conn).upsert_company(
            Company(cik="0000111111", ticker="MSFT", name="Old Issuer Inc.", added_at="t")
        )
    finally:
        conn.close()

    response = client.post("/api/companies", json={"ticker": "MSFT"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ticker_identity_conflict"
    conn = init_db(str(db_path))
    try:
        # Nothing tracked at all — emphatically not the stale issuer.
        assert Repo(conn).list_tracked_ciks() == []
    finally:
        conn.close()


def test_analyzing_a_published_filing_is_a_no_op_that_keeps_it_published(tmp_path, monkeypatch):
    """Filing state is shared by every account tracking the issuer.

    A per-company run used to re-select a verified filing, and a download failure on
    that fresh attempt unpublished the result for everyone.
    """
    db_path = tmp_path / "finwatch.db"
    build_demo_db(str(db_path)).close()
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("FINWATCH_MODEL", "openai/test")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-key")

    def unreachable(_self, _url, **_kwargs):
        raise AssertionError("a published filing must not be downloaded again")

    monkeypatch.setattr(EdgarClient, "fetch_primary_doc", unreachable)
    client = TestClient(
        create_app(db_path=str(db_path), web_dist=tmp_path / "missing-dist"),
        headers=LOCAL_BROWSER_HEADERS,
    )
    accession = "0000950170-24-048288"
    before = client.get(f"/api/filings/{accession}").json()
    assert before["filing"]["outcome"] == "published"

    job = client.post("/api/jobs/analyze", json={"ticker": "MSFT"}).json()
    for _ in range(400):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["state"] not in {"queued", "running"}:
            break
        time.sleep(0.005)

    assert job["state"] == "completed"
    assert [item["reason"] for item in job["items"]] == ["newest_already_analyzed"]
    assert client.get(f"/api/filings/{accession}").json() == before


def _stripe_client(tmp_path, monkeypatch, subscriptions):
    """A local app with billing configured and Stripe's subscription list faked.

    ``subscriptions`` maps a customer id to the list Stripe would return now.
    """
    import hashlib
    import hmac

    from finwatch.db import User
    from finwatch.product.providers import ProviderError, StripeClient

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_operator")
    fetched = []
    client, db_path = _client(tmp_path)

    def fake_list(_self, customer_id):
        # Fetch and write must happen under the delivery lock.
        assert client.app.state.billing_lock.locked()
        fetched.append(customer_id)
        if customer_id not in subscriptions:
            raise ProviderError("Stripe request failed")
        return subscriptions[customer_id]

    monkeypatch.setattr(StripeClient, "customer_subscriptions", fake_list)
    conn = init_db(str(db_path))
    repo = Repo(conn)
    for user_id in ("alice", "bob"):
        repo.create_user(
            User(
                id=user_id, email=f"{user_id}@example.com",
                created_at="2026-11-01T00:00:00+00:00",
                last_login_at="2026-11-01T00:00:00+00:00",
            )
        )
    conn.close()

    def send(event_type, obj):
        body = json.dumps({"type": event_type, "data": {"object": obj}}).encode()
        timestamp = int(time.time())
        signature = hmac.new(
            b"whsec_test", f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        return client.post(
            "/api/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": f"t={timestamp},v1={signature}"},
        )

    def billing(user_id):
        connection = init_db(str(db_path))
        try:
            row = connection.execute(
                "SELECT * FROM billing_accounts WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    return send, billing, fetched


def _subscription(sub_id, status, customer="cus_alice", price="price_founding", created=1):
    return {
        "id": sub_id, "customer": customer, "status": status, "created": created,
        "items": {"data": [{"price": {"id": price}}]},
    }


def _checkout(user_id="alice", customer="cus_alice", subscription="sub_1"):
    return {
        "mode": "subscription", "client_reference_id": user_id,
        "customer": customer, "subscription": subscription,
    }


def _state(row):
    return (row["status"], row["subscription_id"]) if row else None


def test_stripe_checkout_records_the_customers_current_subscription(tmp_path, monkeypatch):
    stripe = {"cus_alice": [_subscription("sub_1", "trialing")]}
    send, billing, fetched = _stripe_client(tmp_path, monkeypatch, stripe)

    assert send("checkout.session.completed", _checkout()).status_code == 204

    assert fetched == ["cus_alice"]
    row = billing("alice")
    assert (row["stripe_customer_id"], row["subscription_id"], row["status"]) == (
        "cus_alice", "sub_1", "trialing"
    )
    assert row["price_id"] == "price_founding"


def test_non_subscription_stripe_events_never_change_billing(tmp_path, monkeypatch):
    """invoice.paid carries status "paid", which used to be stored as the free plan."""
    stripe = {"cus_alice": [_subscription("sub_1", "active")]}
    send, billing, fetched = _stripe_client(tmp_path, monkeypatch, stripe)
    send("checkout.session.completed", _checkout())
    fetched.clear()

    for event_type, obj in (
        ("invoice.paid", {"customer": "cus_alice", "subscription": "sub_1", "status": "paid"}),
        ("payment_intent.succeeded", {"customer": "cus_alice", "status": "succeeded"}),
        ("customer.updated", {"id": "cus_alice", "customer": "cus_alice"}),
    ):
        assert send(event_type, obj).status_code == 204

    assert fetched == []
    assert _state(billing("alice")) == ("active", "sub_1")


def test_subscription_events_use_current_state_not_the_event_payload(tmp_path, monkeypatch):
    """Stripe delivers out of order: an old "active" payload must not undo a cancellation."""
    stripe = {"cus_alice": [_subscription("sub_1", "active")]}
    send, billing, _fetched = _stripe_client(tmp_path, monkeypatch, stripe)
    send("checkout.session.completed", _checkout())

    stripe["cus_alice"] = [_subscription("sub_1", "past_due")]
    send("customer.subscription.updated", _subscription("sub_1", "active"))
    assert _state(billing("alice")) == ("past_due", "sub_1")

    stripe["cus_alice"] = [_subscription("sub_1", "canceled")]
    send("customer.subscription.deleted", _subscription("sub_1", "canceled"))
    send("customer.subscription.updated", _subscription("sub_1", "active"))
    row = billing("alice")
    assert (row["status"], row["subscription_id"], row["price_id"]) == ("free", None, None)
    # The customer stays linked, so a new checkout reuses it.
    assert row["stripe_customer_id"] == "cus_alice"


def test_a_late_checkout_for_an_ended_subscription_keeps_the_live_one(tmp_path, monkeypatch):
    """A redelivered checkout for a cancelled subscription used to reset a newer one to free."""
    stripe = {"cus_alice": [_subscription("sub_a", "active")]}
    send, billing, _fetched = _stripe_client(tmp_path, monkeypatch, stripe)
    send("checkout.session.completed", _checkout(subscription="sub_a"))
    stripe["cus_alice"] = [_subscription("sub_a", "canceled")]
    send("customer.subscription.deleted", _subscription("sub_a", "canceled"))
    stripe["cus_alice"] = [
        _subscription("sub_b", "active", created=2), _subscription("sub_a", "canceled"),
    ]
    send("checkout.session.completed", _checkout(subscription="sub_b"))

    assert send("checkout.session.completed", _checkout(subscription="sub_a")).status_code == 204
    assert _state(billing("alice")) == ("active", "sub_b")


def test_an_ended_subscription_falls_back_to_another_live_one(tmp_path, monkeypatch):
    """A subscription created outside the app must not be forgotten when the old one ends."""
    stripe = {"cus_alice": [_subscription("sub_a", "active")]}
    send, billing, _fetched = _stripe_client(tmp_path, monkeypatch, stripe)
    send("checkout.session.completed", _checkout(subscription="sub_a"))
    stripe["cus_alice"] = [
        _subscription("sub_b", "active", created=2), _subscription("sub_a", "active"),
    ]
    send("customer.subscription.created", _subscription("sub_b", "active", created=2))

    stripe["cus_alice"] = [
        _subscription("sub_b", "active", created=2), _subscription("sub_a", "canceled"),
    ]
    send("customer.subscription.deleted", _subscription("sub_a", "canceled"))

    assert _state(billing("alice")) == ("active", "sub_b")


def test_the_most_entitled_live_subscription_wins(tmp_path, monkeypatch):
    stripe = {"cus_alice": [
        _subscription("sub_new_unpaid", "unpaid", created=3),
        _subscription("sub_active", "active", created=1),
        _subscription("sub_old", "canceled", created=0),
    ]}
    send, billing, _fetched = _stripe_client(tmp_path, monkeypatch, stripe)

    send("checkout.session.completed", _checkout(subscription="sub_new_unpaid"))

    assert _state(billing("alice")) == ("active", "sub_active")


def test_stripe_events_cannot_move_a_customer_between_accounts(tmp_path, monkeypatch):
    stripe = {
        "cus_alice": [_subscription("sub_1", "active")],
        "cus_x": [_subscription("sub_2", "active", customer="cus_x")],
    }
    send, billing, _fetched = _stripe_client(tmp_path, monkeypatch, stripe)
    send("checkout.session.completed", _checkout())

    assert send(
        "checkout.session.completed", _checkout(user_id="bob", subscription="sub_2")
    ).status_code == 204
    assert billing("bob") is None
    # A subscription event for a customer no account has linked is ignored too.
    send("customer.subscription.created", _subscription("sub_2", "active", customer="cus_x"))
    assert _state(billing("alice")) == ("active", "sub_1")


def test_stripe_fetch_failure_returns_503_so_stripe_retries(tmp_path, monkeypatch):
    send, billing, fetched = _stripe_client(tmp_path, monkeypatch, {})

    response = send("checkout.session.completed", _checkout())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "billing_provider_failed"
    assert fetched == ["cus_alice"]
    assert billing("alice") is None
