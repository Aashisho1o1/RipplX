"""FastAPI application exposing finwatch services to the local RipplX UI."""

from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from finwatch.config import Config
from finwatch.db import Repo, init_db
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.ingest import TickerNotFoundError, build_service
from finwatch.presentation import PresentationService
from finwatch.web.jobs import JobConflictError, JobItem, JobRegistry
from finwatch.web.runtime import (
    SETTING_PERIOD,
    SETTING_USER_AGENT,
    RuntimeSecrets,
    resolve_settings,
)
from finwatch.web.security import LOCAL_ALLOWED_HOSTS, remote_allowed_hosts, remote_auth_token


class ApiProblem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class HoldingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=15, pattern=r"^[A-Za-z][A-Za-z0-9.-]*$")


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sec_user_agent: str | None = Field(default=None, max_length=256)
    period: Literal["30d", "60d", "90d", "180d", "1y"] | None = None
    api_key: str | None = Field(default=None, max_length=512)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str | None = Field(
        default=None,
        min_length=1,
        max_length=15,
        pattern=r"^[A-Za-z][A-Za-z0-9.-]*$",
    )


def _since_for_period(period: str) -> str:
    days = {"30d": 30, "60d": 60, "90d": 90, "180d": 180, "1y": 365}.get(period, 90)
    return (date.today() - timedelta(days=days)).isoformat()


def _trimmed(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def _compute_synced_metrics(service, cik: str, *, as_of: str) -> int:
    """Persist deterministic XBRL metrics after a web sync and return the usable count."""
    from finwatch.metrics.service import MetricsService

    bundle, _ = MetricsService(
        service.repo,
        lambda selected_cik: service.edgar.companyfacts(selected_cik),
    ).compute_and_store(cik, as_of=as_of)
    return sum(result.status.value == "computed" for result in bundle.all_results())


def create_app(
    *,
    db_path: str | None = None,
    web_dist: str | Path | None = None,
    remote: bool | None = None,
    auth_token: str | None = None,
    allowed_hosts: list[str] | None = None,
):
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.middleware.trustedhost import TrustedHostMiddleware
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - only hit without the web extra
        raise RuntimeError("Install RipplX web dependencies with `uv sync --extra web`.") from exc

    resolved_db = db_path or os.environ.get("FINWATCH_DB", "./data/finwatch.db")
    if remote is None:
        remote = os.environ.get("FINWATCH_REMOTE", "").strip().lower() in {"1", "true", "yes"}
    configured_dist = os.environ.get("FINWATCH_WEB_DIST")
    default_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    dist = Path(web_dist if web_dist is not None else configured_dist or default_dist)
    app = FastAPI(
        title="RipplX local API",
        version="0.1.0",
        docs_url=None if remote else "/docs",
        redoc_url=None if remote else "/redoc",
        openapi_url=None if remote else "/openapi.json",
    )
    app.state.db_path = resolved_db
    app.state.secrets = RuntimeSecrets()
    app.state.jobs = JobRegistry()
    app.state.remote = remote
    expected_token = remote_auth_token(auth_token) if remote else None
    trusted_hosts = remote_allowed_hosts(allowed_hosts) if remote else list(LOCAL_ALLOWED_HOSTS)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def authenticate_remote_api(request, call_next):
        if remote and request.url.path.startswith("/api/"):
            authorization = request.headers.get("authorization", "")
            scheme, _, candidate = authorization.partition(" ")
            authenticated = (
                scheme.lower() == "bearer"
                and bool(candidate)
                and expected_token is not None
                and secrets.compare_digest(candidate, expected_token)
            )
            if not authenticated:
                return JSONResponse(
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                    content={
                        "error": {
                            "code": "authentication_required",
                            "message": "A valid hosted-alpha access token is required.",
                        }
                    },
                )
        return await call_next(request)

    @app.middleware("http")
    async def same_origin_mutations(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                origin_host = parsed.netloc.lower()
                request_host = request.headers.get("host", "").lower()
                dev_hosts = set() if remote else {"127.0.0.1:5173", "localhost:5173"}
                if origin_host != request_host and origin_host not in dev_hosts:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": {
                                "code": "origin_not_allowed",
                                "message": "Mutation origin is not allowed.",
                            }
                        },
                    )
        return await call_next(request)

    @app.exception_handler(ApiProblem)
    async def api_problem_handler(_request, exc: ApiProblem):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @contextmanager
    def repo_context(demo: bool = False):
        connection = build_demo_db() if demo else init_db(app.state.db_path)
        try:
            yield Repo(connection)
        finally:
            connection.close()

    def settings_payload(repo: Repo) -> dict[str, Any]:
        settings = resolve_settings(repo, app.state.secrets)
        return {
            "setup_required": not bool(settings.sec_user_agent),
            "sec_user_agent": settings.sec_user_agent or "",
            "period": settings.period,
            "model": settings.model or "",
            "api_key_configured": settings.api_key_configured,
            "api_key_source": settings.api_key_source,
            "analysis_configured": bool(settings.model and settings.api_key_configured),
        }

    @app.get("/api/bootstrap")
    def bootstrap():
        with repo_context() as repo:
            return settings_payload(repo)

    @app.get("/api/brief")
    def brief(
        demo: bool = False,
        since: str | None = None,
        until: str | None = None,
    ):
        with repo_context(demo) as repo:
            if demo:
                since = since or DEMO_SINCE
            else:
                settings = resolve_settings(repo, app.state.secrets)
                since = since or _since_for_period(settings.period)
            return PresentationService(repo).brief(
                since=since,
                until=until,
                sample_data=demo,
            )

    @app.get("/api/filings/{accession}")
    def filing_detail(accession: str, demo: bool = False):
        with repo_context(demo) as repo:
            result = PresentationService(repo).filing(accession)
            if result is None:
                raise ApiProblem(404, "filing_not_found", "Filing not found.")
            return result

    @app.get("/api/holdings")
    def holdings():
        with repo_context() as repo:
            return PresentationService(repo).holdings()

    @app.post("/api/holdings", status_code=201)
    def create_holding(payload: HoldingCreate):
        with repo_context() as repo:
            settings = resolve_settings(repo, app.state.secrets)
        if not settings.sec_user_agent:
            raise ApiProblem(
                409, "missing_user_agent", "Configure the SEC User-Agent before adding a company."
            )
        config = Config(
            sec_user_agent=settings.sec_user_agent,
            db_path=app.state.db_path,
            model=settings.model,
        )
        connection, service = build_service(config)
        try:
            company = service.add_holding(payload.ticker)
        except TickerNotFoundError as exc:
            raise ApiProblem(404, "ticker_not_found", "Ticker not found on EDGAR.") from exc
        finally:
            service.edgar.close()
            connection.close()
        with repo_context() as repo:
            view = PresentationService(repo).holdings()
            rows = view.owned + view.watching
            return next(row for row in rows if row.ticker == company.ticker)

    @app.delete("/api/holdings/{ticker}", status_code=204)
    def delete_holding(ticker: str):
        with repo_context() as repo:
            company = repo.get_company_by_ticker(ticker)
            if company is None or not repo.delete_holding(company.cik):
                raise ApiProblem(404, "holding_not_found", "Holding not found.")

    @app.get("/api/companies/{ticker}/metrics")
    def company_metrics(ticker: str, as_of: str | None = None, demo: bool = False):
        selected_date = as_of or date.today().isoformat()
        with repo_context(demo) as repo:
            result = PresentationService(repo).metrics(ticker, as_of=selected_date)
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return result

    @app.get("/api/settings")
    def get_settings():
        with repo_context() as repo:
            return settings_payload(repo)

    @app.put("/api/settings")
    def update_settings(payload: SettingsUpdate):
        with repo_context() as repo:
            if "sec_user_agent" in payload.model_fields_set:
                user_agent = _trimmed(payload.sec_user_agent)
                if user_agent is None or "@" not in user_agent:
                    raise ApiProblem(
                        422,
                        "invalid_user_agent",
                        "Enter an SEC User-Agent containing a contact email.",
                    )
                repo.set_setting(SETTING_USER_AGENT, user_agent)
            if payload.period is not None:
                repo.set_setting(SETTING_PERIOD, payload.period)
            if "api_key" in payload.model_fields_set:
                app.state.secrets.set_api_key(payload.api_key)
            return settings_payload(repo)

    def sync_work(ticker: str | None):
        def work(job_id: str, registry: JobRegistry) -> bool:
            with repo_context() as repo:
                settings = resolve_settings(repo, app.state.secrets)
            if not settings.sec_user_agent:
                raise RuntimeError("SEC User-Agent is not configured.")
            config = Config(sec_user_agent=settings.sec_user_agent, db_path=app.state.db_path)
            connection, service = build_service(config)
            partial = False
            try:
                ciks = service.repo.list_tracked_ciks()
                if ticker:
                    company = service.repo.get_company_by_ticker(ticker)
                    if not company or company.cik not in ciks:
                        raise RuntimeError(f"{ticker.upper()} is not tracked.")
                    ciks = [company.cik]
                for cik in ciks:
                    company = service.repo.get_company(cik)
                    key = company.ticker if company else cik
                    result = service.ingest_one(cik)
                    partial = partial or bool(result.error)
                    metrics_error = None
                    metrics_computed = 0
                    try:
                        metrics_computed = _compute_synced_metrics(
                            service, cik, as_of=date.today().isoformat()
                        )
                    except Exception as exc:  # noqa: BLE001 - preserve successful ingest work
                        partial = True
                        metrics_error = str(exc)
                    details = (
                        f"{result.filings_indexed} filings ({result.filings_new} new), "
                        f"{result.xbrl_facts} XBRL facts, "
                        f"{metrics_computed} verified metrics"
                    )
                    message = result.error or details
                    if result.error and metrics_computed:
                        message += f"; {metrics_computed} verified metrics computed"
                    if metrics_error:
                        message += f"; metrics unavailable: {metrics_error}"
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=key,
                            state="failed" if result.error or metrics_error else "completed",
                            message=message,
                        ),
                    )
            finally:
                service.edgar.close()
                connection.close()
            return partial

        return work

    def analysis_work(ticker: str | None):
        def work(job_id: str, registry: JobRegistry) -> bool:
            from pathlib import Path as FilePath

            from finwatch.db import Repo as DatabaseRepo
            from finwatch.ingest import EdgarClient
            from finwatch.llm.router import LiteLLMClient
            from finwatch.pipeline.run import (
                build_orchestrator,
                holding_records,
                newest_filing_to_analyze,
                process_filing,
            )

            connection = init_db(app.state.db_path)
            repo = DatabaseRepo(connection)
            settings = resolve_settings(repo, app.state.secrets)
            if not settings.sec_user_agent:
                connection.close()
                raise RuntimeError("SEC User-Agent is not configured.")
            if not settings.model:
                connection.close()
                raise RuntimeError("Analysis model is not configured.")
            if not settings.api_key_configured:
                connection.close()
                raise RuntimeError("A provider API key is not configured.")
            cik = None
            if ticker:
                company = repo.get_company_by_ticker(ticker)
                if not company:
                    connection.close()
                    raise RuntimeError(f"{ticker.upper()} is not tracked.")
                cik = company.cik
            cache = (
                FilePath(app.state.db_path).parent / "cache"
                if app.state.db_path != ":memory:"
                else None
            )
            edgar = EdgarClient(settings.sec_user_agent, cache_dir=cache)
            key = app.state.secrets.api_key()
            llm = LiteLLMClient(settings.model, api_key=key)
            orchestrator = build_orchestrator(
                repo,
                llm=llm,
                companyfacts_provider=lambda selected_cik: edgar.companyfacts(selected_cik),
                model=settings.model,
            )
            partial = False
            try:
                records = holding_records(repo)
                filing = newest_filing_to_analyze(repo, cik)
                if filing is None:
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=ticker.upper() if ticker else "portfolio",
                            state="completed",
                            message="The newest supported filing is already terminal.",
                        ),
                    )
                else:
                    company = repo.get_company(filing.cik)
                    filing_key = (
                        f"{company.ticker if company else filing.cik} "
                        f"{filing.accession_number}"
                    )

                    def progress(stage, state, message, diagnostics, key=filing_key):
                        registry.upsert_item(
                            job_id,
                            JobItem(
                                key=f"{key}:{stage}",
                                state=state,
                                message=message,
                                stage=stage,
                                diagnostics=diagnostics,
                            ),
                        )

                    def fetch(url):
                        return edgar.fetch_primary_doc(url).decode("utf-8", "replace")
                    result = process_filing(
                        orchestrator,
                        repo,
                        filing,
                        fetch_html=fetch,
                        records=records,
                        on_stage=progress,
                    )
                    partial = partial or not result.ok or result.manual_review
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=f"{result.ticker} {result.accession}",
                            state="completed"
                            if result.ok and not result.manual_review
                            else "failed",
                            message=result.error
                            or (
                                "manual review required"
                                if result.manual_review
                                else result.verdict or "complete"
                            ),
                            verdict=result.verdict,
                        ),
                    )
            finally:
                edgar.close()
                connection.close()
            return partial

        return work

    @app.post("/api/jobs/sync", status_code=202)
    def start_sync(payload: JobRequest):
        try:
            return app.state.jobs.start("sync", sync_work(_trimmed(payload.ticker)))
        except JobConflictError as exc:
            raise ApiProblem(409, "job_conflict", str(exc)) from exc

    @app.post("/api/jobs/analyze", status_code=202)
    def start_analysis(payload: JobRequest):
        with repo_context() as repo:
            settings = resolve_settings(repo, app.state.secrets)
        if not settings.model or not settings.api_key_configured:
            raise ApiProblem(
                409, "model_not_configured", "Configure the analysis model and API key first."
            )
        try:
            return app.state.jobs.start(
                "analysis",
                analysis_work(_trimmed(payload.ticker)),
            )
        except JobConflictError as exc:
            raise ApiProblem(409, "job_conflict", str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = app.state.jobs.get(job_id)
        if job is None:
            raise ApiProblem(404, "job_not_found", "Job not found.")
        return job

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def unknown_api(path: str):
        raise ApiProblem(404, "api_route_not_found", f"API route /api/{path} was not found.")

    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            candidate = dist / path
            if path and candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
