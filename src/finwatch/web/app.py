"""FastAPI application exposing finwatch services to the local RipplX UI."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from finwatch.config import Config
from finwatch.db import Holding, Repo, init_db
from finwatch.demo import DEMO_SINCE, build_demo_db
from finwatch.ingest import TickerNotFoundError, build_service
from finwatch.presentation import PresentationService
from finwatch.web.jobs import JobConflictError, JobItem, JobRegistry
from finwatch.web.runtime import (
    SETTING_MODEL_EXTRACT,
    SETTING_MODEL_REASON,
    SETTING_PERIOD,
    SETTING_USER_AGENT,
    RuntimeSecrets,
    resolve_settings,
)


class ApiProblem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class HoldingCreate(BaseModel):
    ticker: str
    owned: bool
    shares: float | None = Field(default=None, gt=0)
    cost_basis: float | None = Field(default=None, ge=0)
    target_weight_pct: float | None = Field(default=None, ge=0, le=100)
    horizon: Literal["trading", "1-3y", "5y+", "indefinite"] | None = None
    thesis: str | None = None


class HoldingUpdate(BaseModel):
    owned: bool | None = None
    shares: float | None = Field(default=None, gt=0)
    cost_basis: float | None = Field(default=None, ge=0)
    target_weight_pct: float | None = Field(default=None, ge=0, le=100)
    horizon: Literal["trading", "1-3y", "5y+", "indefinite"] | None = None
    thesis: str | None = None


class SettingsUpdate(BaseModel):
    sec_user_agent: str | None = None
    period: Literal["30d", "60d", "90d", "180d", "1y"] | None = None
    model_extract: str | None = None
    model_reason: str | None = None
    api_key: str | None = None


class JobRequest(BaseModel):
    ticker: str | None = None
    accession: str | None = None
    mode: Literal["auto", "parse", "analysis"] = "auto"
    limit: int = Field(default=1, ge=1, le=10)
    form: Literal["8-K", "10-Q", "10-K"] | None = None   # None = newest of any form


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
        service.repo,
        lambda selected_cik: service.edgar.companyfacts(selected_cik),
    ).compute_and_store(cik, as_of=as_of)
    return sum(result.status.value == "computed" for result in bundle.all_results())


def create_app(*, db_path: str | None = None, web_dist: str | Path | None = None):
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - only hit without the web extra
        raise RuntimeError("Install RipplX web dependencies with `uv sync --extra web`.") from exc

    resolved_db = db_path or os.environ.get("FINWATCH_DB", "./data/finwatch.db")
    configured_dist = os.environ.get("FINWATCH_WEB_DIST")
    default_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    dist = Path(web_dist if web_dist is not None else configured_dist or default_dist)
    app = FastAPI(title="RipplX local API", version="0.1.0")
    app.state.db_path = resolved_db
    app.state.secrets = RuntimeSecrets()
    app.state.jobs = JobRegistry()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def same_origin_mutations(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                origin_host = parsed.netloc.lower()
                request_host = request.headers.get("host", "").lower()
                dev_hosts = {"127.0.0.1:5173", "localhost:5173"}
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
            "model_extract": settings.model_extract or "",
            "model_reason": settings.model_reason or "",
            "api_key_configured": settings.api_key_configured,
            "api_key_source": settings.api_key_source,
            "analysis_configured": bool(
                settings.model_extract and settings.model_reason and settings.api_key_configured
            ),
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

    @app.post("/api/filings/{accession}/reverify")
    def reverify_filing(accession: str):
        from finwatch.pipeline.run import reverify

        with repo_context() as repo:
            report = reverify(repo, accession)
            if report is None:
                raise ApiProblem(
                    404, "analysis_not_found", "No stored analysis can be re-verified."
                )
            return report

    @app.get("/api/holdings")
    def holdings():
        with repo_context() as repo:
            return PresentationService(repo).holdings()

    @app.post("/api/holdings", status_code=201)
    def create_holding(payload: HoldingCreate):
        if payload.owned and (payload.shares is None or payload.cost_basis is None):
            raise ApiProblem(
                422, "holding_fields_required", "Owned holdings require shares and cost basis."
            )
        with repo_context() as repo:
            settings = resolve_settings(repo, app.state.secrets)
        if not settings.sec_user_agent:
            raise ApiProblem(
                409, "missing_user_agent", "Configure the SEC User-Agent before adding a company."
            )
        config = Config(
            sec_user_agent=settings.sec_user_agent,
            db_path=app.state.db_path,
            model_extract=settings.model_extract,
            model_reason=settings.model_reason,
        )
        connection, service = build_service(config)
        try:
            company = service.add_holding(
                payload.ticker,
                owned=payload.owned,
                shares=payload.shares,
                cost_basis=payload.cost_basis,
                target_weight_pct=payload.target_weight_pct,
                horizon=payload.horizon,
                thesis=_trimmed(payload.thesis),
            )
        except TickerNotFoundError as exc:
            raise ApiProblem(404, "ticker_not_found", "Ticker not found on EDGAR.") from exc
        finally:
            service.edgar.close()
            service.stooq.close()
            connection.close()
        with repo_context() as repo:
            view = PresentationService(repo).holdings()
            rows = view.owned if payload.owned else view.watching
            return next(row for row in rows if row.ticker == company.ticker)

    @app.patch("/api/holdings/{ticker}")
    def update_holding(ticker: str, payload: HoldingUpdate):
        with repo_context() as repo:
            company = repo.get_company_by_ticker(ticker)
            current = repo.get_holding_by_cik(company.cik) if company else None
            if current is None:
                raise ApiProblem(404, "holding_not_found", "Holding not found.")
            values = current.model_dump()
            for name in payload.model_fields_set:
                values[name] = getattr(payload, name)
            values["owned"] = int(values["owned"])
            values["thesis"] = _trimmed(values.get("thesis"))
            if values["owned"] and (
                values.get("shares") is None or values.get("cost_basis") is None
            ):
                raise ApiProblem(
                    422, "holding_fields_required", "Owned holdings require shares and cost basis."
                )
            repo.upsert_holding(Holding(**values))
            view = PresentationService(repo).holdings()
            rows = view.owned if values["owned"] else view.watching
            return next(row for row in rows if row.ticker == current.ticker)

    @app.delete("/api/holdings/{ticker}", status_code=204)
    def delete_holding(ticker: str):
        with repo_context() as repo:
            company = repo.get_company_by_ticker(ticker)
            if company is None or not repo.delete_holding(company.cik):
                raise ApiProblem(404, "holding_not_found", "Holding not found.")

    @app.get("/api/companies/{ticker}/metrics")
    def company_metrics(
        ticker: str, as_of: str | None = None, show_all: bool = False, demo: bool = False
    ):
        selected_date = as_of or date.today().isoformat()
        with repo_context(demo) as repo:
            result = PresentationService(repo).metrics(
                ticker, as_of=selected_date, show_all=show_all
            )
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
            if "model_extract" in payload.model_fields_set:
                repo.set_setting(SETTING_MODEL_EXTRACT, _trimmed(payload.model_extract) or "")
            if "model_reason" in payload.model_fields_set:
                repo.set_setting(SETTING_MODEL_REASON, _trimmed(payload.model_reason) or "")
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
                        f"{result.xbrl_facts} XBRL facts, {result.prices} prices, "
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
                service.stooq.close()
                connection.close()
            return partial

        return work

    def analysis_work(
        ticker: str | None, limit: int, accession: str | None, mode: str,
        form: str | None = None,
    ):
        def work(job_id: str, registry: JobRegistry) -> bool:
            from pathlib import Path as FilePath

            from finwatch.db import Repo as DatabaseRepo
            from finwatch.ingest import EdgarClient
            from finwatch.llm.router import LiteLLMClient
            from finwatch.pipeline.run import (
                build_orchestrator,
                holding_records,
                process_filing,
                process_parsing,
                unanalyzed_filings,
            )

            connection = init_db(app.state.db_path)
            repo = DatabaseRepo(connection)
            settings = resolve_settings(repo, app.state.secrets)
            if not settings.sec_user_agent:
                connection.close()
                raise RuntimeError("SEC User-Agent is not configured.")
            if mode != "parse" and (not settings.model_extract or not settings.model_reason):
                connection.close()
                raise RuntimeError("Analysis models are not configured.")
            if mode != "parse" and not settings.api_key_configured:
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
            orchestrator = None
            if mode != "parse":
                key = app.state.secrets.api_key()
                orchestrator = build_orchestrator(
                    repo,
                    llm_extract=LiteLLMClient(settings.model_extract, api_key=key),
                    llm_reason=LiteLLMClient(settings.model_reason, api_key=key),
                    companyfacts_provider=lambda selected_cik: edgar.companyfacts(selected_cik),
                    price_provider=repo,
                    model_extract=settings.model_extract,
                    model_reason=settings.model_reason,
                )
            partial = False
            try:
                records = holding_records(repo)
                if accession:
                    # An explicit accession pins one filing; the form filter is ignored
                    # here (the accession already determines the form).
                    selected = repo.get_filing(accession)
                    if selected is None:
                        raise RuntimeError(f"Filing {accession} was not found.")
                    if cik is not None and selected.cik != cik:
                        raise RuntimeError(f"Filing {accession} does not belong to {ticker}.")
                    filings = [selected]
                else:
                    # Browser actions process the newest eligible filing(s), optionally
                    # narrowed to one form; CLI backfills keep their oldest-first order.
                    wanted = frozenset({form}) if form else None
                    filings = list(reversed(unanalyzed_filings(repo, cik, forms=wanted)))[:limit]
                if not filings:
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=ticker.upper() if ticker else "portfolio",
                            state="completed",
                            message=f"No unanalyzed {form or '10-K, 10-Q, or 8-K'} filings.",
                        ),
                    )
                for filing in filings:
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
                    if mode == "parse":
                        result = process_parsing(
                            repo, filing, fetch_html=fetch, on_stage=progress
                        )
                    else:
                        assert orchestrator is not None
                        result = process_filing(
                            orchestrator,
                            repo,
                            filing,
                            fetch_html=fetch,
                            records=records,
                            rerun_from="extract" if mode == "analysis" else None,
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
        if payload.mode != "parse" and (
            not settings.model_extract
            or not settings.model_reason
            or not settings.api_key_configured
        ):
            raise ApiProblem(
                409, "models_not_configured", "Configure an analysis model and API key first."
            )
        try:
            return app.state.jobs.start(
                "analysis",
                analysis_work(
                    _trimmed(payload.ticker),
                    payload.limit,
                    _trimmed(payload.accession),
                    payload.mode,
                    payload.form,
                ),
            )
        except JobConflictError as exc:
            raise ApiProblem(409, "job_conflict", str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = app.state.jobs.get(job_id)
        if job is None:
            raise ApiProblem(404, "job_not_found", "Job not found.")
        return job

    @app.get("/api/{path:path}", include_in_schema=False)
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
