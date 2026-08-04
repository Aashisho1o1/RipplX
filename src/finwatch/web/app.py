"""FastAPI application exposing finwatch services to the local RipplX UI."""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from finwatch.config import Config
from finwatch.db import (
    LOCAL_USER_ID,
    Company,
    Repo,
    TickerIdentityConflictError,
    User,
    connect,
    init_db,
)
from finwatch.demo import (
    DEMO_SINCE,
    PUBLIC_SHOWCASE_USER_ID,
    build_demo_db,
    public_showcase_refreshed_at,
)
from finwatch.ingest import TickerNotFoundError, build_service
from finwatch.presentation import PresentationService
from finwatch.product import ProductService
from finwatch.product.models import Thesis
from finwatch.web.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    CsrfCodec,
    EmailDeliveryError,
    EmailOtpManager,
    EmailSender,
    InvalidCsrfError,
    InvalidSessionError,
    OtpRateLimitError,
    OtpVerificationError,
    RequestCodeBody,
    ResendEmailSender,
    SessionCodec,
    VerifyCodeBody,
)
from finwatch.web.jobs import JobConflictError, JobItem, JobRegistry
from finwatch.web.runtime import (
    LOCAL_SESSION_ID,
    SETTING_USER_AGENT,
    environment_api_key,
    provider_for_model,
    resolve_settings,
)
from finwatch.web.security import (
    LOCAL_ALLOWED_HOSTS,
    remote_allowed_hosts,
    remote_auth_secret,
    remote_email_config,
)

REQUEST_BODY_LIMIT_BYTES = 1024 * 1024
MAX_TRACKED_TICKERS = 25
# Path/body parameter shapes (parameterized queries already prevent injection; these
# reject malformed input early and keep GET/DELETE consistent with the POST models).
_TICKER_PATTERN = r"^[A-Za-z][A-Za-z0-9.-]*$"
_ACCESSION_PATTERN = r"^\d{10}-\d{2}-\d{6}$"
_JOB_ID_PATTERN = r"^[0-9a-f]{32}$"
# Unauthenticated, GET-only, read-only routes serving the public SEC showcase. They
# use the reserved showcase owner and fall back to the bundled zero-key fixtures until
# an operator has atomically published a cached live refresh.
PUBLIC_SAMPLE_PREFIX = "/api/public/sample/"
_REQUEST_TOO_LARGE_BODY = (
    b'{"error":{"code":"request_too_large","message":"Request body exceeds the 1 MiB limit."}}'
)


@dataclass(frozen=True)
class RequestPrincipal:
    user_id: str
    session_id: str
    expires_at: int | None


class RequestBodyLimitMiddleware:
    """Bound request bodies before FastAPI or an endpoint attempts to parse them.

    A declared length is rejected without reading. Chunked/streamed requests are
    buffered only up to the same small cap, then replayed to the downstream app.
    """

    def __init__(self, app, max_bytes: int = REQUEST_BODY_LIMIT_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_REQUEST_TOO_LARGE_BODY)).encode("ascii")),
                ],
            }
        )
        await send(
            {"type": "http.response.body", "body": _REQUEST_TOO_LARGE_BODY, "more_body": False}
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                declared_size = 0  # the streamed-byte check remains authoritative
            if declared_size > self.max_bytes:
                await self._reject(send)
                return

        messages: list[dict] = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    await self._reject(send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        index = 0

        async def replay_receive():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


class ApiProblem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class CompanyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=15, pattern=_TICKER_PATTERN)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sec_user_agent: str | None = Field(default=None, max_length=256)
    period: Literal["30d", "60d", "90d", "180d", "1y"] | None = None


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str | None = Field(
        default=None,
        min_length=1,
        max_length=15,
        pattern=_TICKER_PATTERN,
    )
    form_type: Literal["10-K", "10-Q", "8-K"] | None = None


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitoring_enabled: bool
    notification_level: Literal["urgent", "this_week", "weekly", "off"]
    peer_tickers: list[str] = Field(default_factory=list, max_length=6)


class ThesisUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thesis: Thesis


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
    auth_secret: str | None = None,
    allowed_hosts: list[str] | None = None,
    email_sender: EmailSender | None = None,
):
    try:
        from fastapi import FastAPI
        from fastapi import Path as PathParam
        from fastapi.exceptions import RequestValidationError
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.middleware.trustedhost import TrustedHostMiddleware
        from fastapi.responses import FileResponse, JSONResponse, Response
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
    app.state.jobs = JobRegistry()
    app.state.company_add_lock = Lock()
    app.state.remote = remote
    posthog_key = os.environ.get("POSTHOG_PROJECT_KEY", "").strip()
    if posthog_key:
        from finwatch.product.providers import SafeAnalytics

        app.state.analytics = SafeAnalytics(
            posthog_key,
            host=os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com"),
        )
    else:
        app.state.analytics = None
    trusted_hosts = remote_allowed_hosts(allowed_hosts) if remote else list(LOCAL_ALLOWED_HOSTS)
    if remote:
        signing_secret = remote_auth_secret(auth_secret)
        sec_user_agent = _trimmed(os.environ.get("SEC_USER_AGENT"))
        if not sec_user_agent or "@" not in sec_user_agent:
            raise RuntimeError(
                "Remote serving requires SEC_USER_AGENT with an operator contact email."
            )
        if email_sender is None:
            resend_key, from_address = remote_email_config()
            email_sender = ResendEmailSender(
                api_key=resend_key,
                from_address=from_address,
            )
        app.state.otp = EmailOtpManager(
            auth_secret=signing_secret,
            email_sender=email_sender,
        )
        app.state.session_codec = SessionCodec(signing_secret)
        app.state.csrf_codec = CsrfCodec(signing_secret)
    else:
        app.state.otp = None
        app.state.session_codec = None
        app.state.csrf_codec = None

    # Schema work belongs to process startup, never concurrent request/job paths.
    # ``:memory:`` remains a test-only exception because each SQLite connection is
    # an independent ephemeral database.
    if resolved_db != ":memory:":
        startup_connection = init_db(resolved_db)
        startup_connection.close()

    def operational_connection():
        # Test-only in-memory databases cannot survive the startup connection close.
        return (
            init_db(app.state.db_path)
            if app.state.db_path == ":memory:"
            else connect(app.state.db_path)
        )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=([] if remote else ["http://127.0.0.1:5173", "http://localhost:5173"]),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER_NAME],
    )
    # Authentication and origin checks are registered below and therefore wrap
    # this limiter; unauthenticated/cross-origin callers are rejected before we
    # spend memory buffering any request body.
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=REQUEST_BODY_LIMIT_BYTES)

    public_auth_paths = frozenset(
        {"/api/auth/request-code", "/api/auth/verify-code", "/api/webhooks/stripe"}
    )

    @app.middleware("http")
    async def authenticate_remote_api(request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        if not remote:
            request.state.principal = RequestPrincipal(
                user_id=LOCAL_USER_ID,
                session_id=LOCAL_SESSION_ID,
                expires_at=None,
            )
            return await call_next(request)
        if request.url.path in public_auth_paths or request.method == "OPTIONS":
            return await call_next(request)
        # The bundled sample is public: an anonymous visitor must be able to see a real
        # verified brief without an account. The bypass is a GET-only path prefix, never
        # a query parameter on a private route — `?demo=false` on /api/brief would
        # otherwise fall through to the operational database. The handlers under this
        # prefix never read request.state.principal, so no principal is injected here
        # and none can be impersonated.
        if request.method == "GET" and request.url.path.startswith(PUBLIC_SAMPLE_PREFIX):
            return await call_next(request)

        try:
            session = app.state.session_codec.load(request.cookies.get(SESSION_COOKIE_NAME, ""))
        except InvalidSessionError:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "authentication_required",
                        "message": "Sign in with your email to continue.",
                    }
                },
            )
        connection = operational_connection()
        try:
            user_exists = Repo(connection).get_user(session.user_id) is not None
        finally:
            connection.close()
        if not user_exists:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "authentication_required",
                        "message": "Sign in with your email to continue.",
                    }
                },
            )
        request.state.principal = RequestPrincipal(
            user_id=session.user_id,
            session_id=session.session_id,
            expires_at=session.expires_at,
        )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                app.state.csrf_codec.validate(
                    cookie_token=request.cookies.get(CSRF_COOKIE_NAME),
                    header_token=request.headers.get(CSRF_HEADER_NAME),
                    session=session,
                )
            except InvalidCsrfError:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "csrf_rejected",
                            "message": "Refresh the page and try again.",
                        }
                    },
                )
        return await call_next(request)

    @app.middleware("http")
    async def same_origin_mutations(request, call_next):
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path != "/api/webhooks/stripe"
        ):
            origin = request.headers.get("origin")
            if not origin:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "origin_required",
                            "message": "Browser mutations require an Origin header.",
                        }
                    },
                )
            if origin:
                parsed = urlsplit(origin)
                origin_host = parsed.netloc.lower()
                request_host = request.headers.get("host", "").lower()
                dev_hosts = set() if remote else {"127.0.0.1:5173", "localhost:5173"}
                if (
                    parsed.scheme not in {"http", "https"}
                    or parsed.username is not None
                    or (origin_host != request_host and origin_host not in dev_hosts)
                ):
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

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; img-src 'self' data:; script-src 'self'; "
            "style-src 'self'; connect-src 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if remote:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(ApiProblem)
    async def api_problem_handler(_request, exc: ApiProblem):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request, exc: RequestValidationError):
        fields = [".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "fields": fields,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request, _exc: Exception):
        """Keep internal/provider exception text out of the public API contract."""
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The request could not be completed.",
                }
            },
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @contextmanager
    def repo_context(demo: bool = False):
        connection = operational_connection()
        if demo:
            live_repo = Repo(connection)
            if public_showcase_refreshed_at(live_repo) is None:
                connection.close()
                connection = build_demo_db()
        try:
            yield Repo(connection)
        finally:
            connection.close()

    def principal_for(request: Request) -> RequestPrincipal:
        return request.state.principal

    def sample_scope(principal: RequestPrincipal, demo: bool) -> str:
        """Project public SEC artifacts only through the reserved showcase owner."""
        return PUBLIC_SHOWCASE_USER_ID if demo else principal.user_id

    def capture(principal: RequestPrincipal, event: str, **properties: str) -> None:
        analytics = app.state.analytics
        if analytics is not None:
            analytics.capture(principal.user_id, event, properties)

    def settings_payload(
        repo: Repo,
        principal: RequestPrincipal,
    ) -> dict[str, Any]:
        settings = resolve_settings(
            repo,
            user_id=principal.user_id,
            remote=remote,
        )
        user = repo.get_user(principal.user_id) if remote else None
        billing = (
            ProductService(repo, user_id=principal.user_id).store.billing_account()
            if remote
            else None
        )
        return {
            "setup_required": False if remote else not bool(settings.sec_user_agent),
            # The operator's SEC contact is not participant account data.
            "sec_user_agent": "" if remote else settings.sec_user_agent or "",
            "account_email": user.email if user else None,
            "period": settings.period,
            "model": settings.model or "",
            "provider": provider_for_model(settings.model),
            "api_key_configured": settings.api_key_configured,
            "analysis_configured": bool(settings.model and settings.api_key_configured),
            "billing_configured": bool(
                os.environ.get("STRIPE_SECRET_KEY") and os.environ.get("STRIPE_PRICE_ID")
            ),
            "billing_status": billing.get("status", "free") if billing else "free",
            "showcase_source": None,
            "showcase_updated_at": None,
        }

    def tracked_job_ticker(repo: Repo, user_id: str, ticker: str | None) -> str | None:
        selected = _trimmed(ticker)
        if selected is None:
            return None
        company = repo.get_company_by_ticker(selected)
        if company is None or repo.get_user_company(user_id, company.cik) is None:
            raise ApiProblem(404, "company_not_found", "Company not found.")
        return company.ticker

    @app.post("/api/auth/request-code", status_code=202)
    def request_code(payload: RequestCodeBody):
        if not remote or app.state.otp is None:
            raise ApiProblem(404, "api_route_not_found", "API route was not found.")
        try:
            challenge = app.state.otp.request_code(payload.email)
        except OtpRateLimitError as exc:
            raise ApiProblem(429, "code_rate_limited", str(exc)) from exc
        except EmailDeliveryError as exc:
            raise ApiProblem(503, "email_delivery_failed", str(exc)) from exc
        return {
            "challenge_id": challenge.challenge_id,
            "expires_in": max(0, challenge.expires_at - int(datetime.now(UTC).timestamp())),
        }

    @app.post("/api/auth/verify-code", status_code=204)
    def verify_code(request: Request, payload: VerifyCodeBody):
        if not remote or app.state.otp is None:
            raise ApiProblem(404, "api_route_not_found", "API route was not found.")
        try:
            email = app.state.otp.verify_code(payload.challenge_id, payload.code)
        except OtpVerificationError as exc:
            raise ApiProblem(401, "invalid_code", str(exc)) from exc
        now = datetime.now(UTC).isoformat()
        with repo_context() as repo:
            user = repo.get_user_by_email(email)
            if user is None:
                repo.create_user(
                    User(
                        id=uuid.uuid4().hex,
                        email=email,
                        created_at=now,
                        last_login_at=now,
                    )
                )
                user = repo.get_user_by_email(email)
            if user is None:  # pragma: no cover - guarded by unique insert/query
                raise RuntimeError("User creation failed.")
            repo.update_user_last_login(user.id, at=now)
        issued = app.state.session_codec.issue(user.id)
        csrf_token = app.state.csrf_codec.issue(issued.identity)
        response = Response(status_code=204)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            issued.token,
            max_age=SESSION_TTL_SECONDS,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf_token,
            max_age=SESSION_TTL_SECONDS,
            path="/",
            secure=True,
            httponly=False,
            samesite="lax",
        )
        return response

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request):
        response = Response(status_code=204)
        response.delete_cookie(
            SESSION_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax"
        )
        response.delete_cookie(
            CSRF_COOKIE_NAME, path="/", secure=True, httponly=False, samesite="lax"
        )
        return response

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        principal = principal_for(request)
        with repo_context() as repo:
            payload = settings_payload(repo, principal)
        if not remote or principal.expires_at is None:
            return payload
        identity = app.state.session_codec.load(request.cookies.get(SESSION_COOKIE_NAME, ""))
        response = JSONResponse(content=payload)
        response.set_cookie(
            CSRF_COOKIE_NAME,
            app.state.csrf_codec.issue(identity),
            max_age=max(
                1,
                principal.expires_at - int(datetime.now(UTC).timestamp()),
            ),
            path="/",
            secure=True,
            httponly=False,
            samesite="lax",
        )
        return response

    @app.get("/api/brief")
    def brief(request: Request, demo: bool = False):
        principal = principal_for(request)
        with repo_context(demo) as repo:
            if demo:
                since_value = (
                    _since_for_period("1y")
                    if public_showcase_refreshed_at(repo)
                    else DEMO_SINCE
                )
            else:
                settings = resolve_settings(
                    repo,
                    user_id=principal.user_id,
                    remote=remote,
                )
                since_value = _since_for_period(settings.period)
            return PresentationService(repo, user_id=sample_scope(principal, demo)).brief(
                since=since_value,
                sample_data=demo,
            )

    @app.get("/api/filings/{accession}")
    def filing_detail(
        request: Request,
        accession: str = PathParam(pattern=_ACCESSION_PATTERN),
        demo: bool = False,
    ):
        principal = principal_for(request)
        with repo_context(demo) as repo:
            result = PresentationService(repo, user_id=sample_scope(principal, demo)).filing(
                accession, sample_data=demo
            )
            if result is None:
                raise ApiProblem(404, "filing_not_found", "Filing not found.")
            return result

    @app.get("/api/filings/{accession}/certificate")
    def filing_certificate(
        request: Request,
        accession: str = PathParam(pattern=_ACCESSION_PATTERN),
        download: bool = False,
        demo: bool = False,
    ):
        principal = principal_for(request)
        with repo_context(demo) as repo:
            result = PresentationService(repo, user_id=sample_scope(principal, demo)).certificate(
                accession
            )
            if result is None:
                raise ApiProblem(404, "certificate_not_found", "Certificate not found.")
            if not download:
                return result
            return Response(
                content=result.model_dump_json(indent=2),
                media_type="application/json",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="ripplx-{accession}-certificate.json"'
                    )
                },
            )

    @app.get("/api/companies")
    def companies(request: Request, demo: bool = False):
        principal = principal_for(request)
        with repo_context(demo) as repo:
            return PresentationService(
                repo, user_id=sample_scope(principal, demo)
            ).companies()

    @app.post("/api/companies", status_code=201)
    def create_company(request: Request, payload: CompanyCreate):
        principal = principal_for(request)
        # Registration includes external EDGAR reads. Serialize it process-wide so
        # concurrent requests cannot multiply request rate or race the launch cap.
        with app.state.company_add_lock:
            with repo_context() as repo:
                settings = resolve_settings(
                    repo,
                    user_id=principal.user_id,
                    remote=remote,
                )
                if not settings.sec_user_agent:
                    raise ApiProblem(
                        409,
                        "missing_user_agent",
                        "Configure the SEC User-Agent before adding a company.",
                    )
                # Cheap pre-check only: refuse a full workspace before spending an EDGAR
                # read. A symbol whose stored row the user already tracks may still be a
                # no-op re-add, so it falls through to real resolution below.
                known = repo.get_company_by_ticker(payload.ticker)
                if repo.count_tracked_companies(principal.user_id) >= MAX_TRACKED_TICKERS \
                        and not (
                            known and repo.get_user_company(principal.user_id, known.cik)
                        ):
                    raise ApiProblem(
                        409,
                        "tracked_ticker_limit",
                        f"Each workspace is limited to {MAX_TRACKED_TICKERS} tracked tickers.",
                    )
            # Issuer identity comes from the current SEC index, never from the stored
            # row. Trusting a local ticker match lets a recycled symbol silently track
            # the issuer that used to file under it.
            config = Config(
                sec_user_agent=settings.sec_user_agent,
                db_path=app.state.db_path,
                model=settings.model,
            )
            connection, service = build_service(config, conn=operational_connection())
            try:
                record = service.resolve_identity(payload.ticker)
            except TickerNotFoundError as exc:
                raise ApiProblem(
                    404, "ticker_not_found", "Ticker not found on EDGAR."
                ) from exc
            finally:
                service.edgar.close()
                connection.close()
            with repo_context() as repo:
                if known is not None and known.cik != record.cik:
                    raise ApiProblem(
                        409,
                        "ticker_identity_conflict",
                        f"{record.ticker} is on file for a different issuer. "
                        "Remove the stale entry before tracking this one.",
                    )
                already_tracked = bool(
                    repo.get_user_company(principal.user_id, record.cik)
                )
                if (
                    not already_tracked
                    and repo.count_tracked_companies(principal.user_id) >= MAX_TRACKED_TICKERS
                ):
                    raise ApiProblem(
                        409,
                        "tracked_ticker_limit",
                        f"Each workspace is limited to {MAX_TRACKED_TICKERS} tracked tickers.",
                    )
                now = datetime.now(UTC).isoformat()
                try:
                    repo.upsert_company(Company(
                        cik=record.cik, ticker=record.ticker,
                        name=record.title, added_at=now,
                    ))
                except TickerIdentityConflictError as exc:
                    raise ApiProblem(
                        409,
                        "ticker_identity_conflict",
                        f"{record.ticker} is on file for a different issuer. "
                        "Remove the stale entry before tracking this one.",
                    ) from exc
                repo.track_company(record.cik, at=now, user_id=principal.user_id)
        with repo_context() as repo:
            view = PresentationService(repo, user_id=principal.user_id).companies()
            # Match on CIK: share classes collapse to one issuer, so the stored label
            # can legitimately differ from the symbol the caller submitted.
            return next(row for row in view.companies if row.cik == record.cik)

    @app.delete("/api/companies/{ticker}", status_code=204)
    def delete_company(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            company = repo.get_company_by_ticker(ticker)
            if company is None or not repo.untrack_company(company.cik, user_id=principal.user_id):
                raise ApiProblem(404, "company_not_found", "Company not found.")

    @app.get("/api/companies/{ticker}/metrics")
    def company_metrics(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
        as_of: date | None = None,
        demo: bool = False,
    ):
        principal = principal_for(request)
        selected_date = as_of.isoformat() if as_of else date.today().isoformat()
        with repo_context(demo) as repo:
            result = PresentationService(repo, user_id=sample_scope(principal, demo)).metrics(
                ticker, as_of=selected_date
            )
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            capture(
                principal,
                "research_opened",
                surface="company",
                state="demo" if demo else "live",
            )
            return result

    @app.get("/api/companies/{ticker}/research")
    def company_research(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
        demo: bool = False,
    ):
        principal = principal_for(request)
        with repo_context(demo) as repo:
            result = ProductService(
                repo, user_id=sample_scope(principal, demo)
            ).before_you_buy(ticker)
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return result

    @app.post("/api/companies/{ticker}/research-runs", status_code=202)
    def create_company_research_run(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        from finwatch.llm.router import LiteLLMClient
        from finwatch.product.research import (
            CompanyResearchHarness,
            ResearchHarnessError,
            research_input_hash,
        )

        principal = principal_for(request)
        with repo_context() as repo:
            settings = resolve_settings(repo, user_id=principal.user_id, remote=remote)
            if not settings.model or not settings.api_key_configured:
                raise ApiProblem(
                    409, "analysis_not_configured", "Analysis is temporarily unavailable."
                )
            service = ProductService(repo, user_id=principal.user_id)
            company = service._company(ticker)
            if company is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            filing = service._latest_supported(company)
            if filing is None or filing.status != "verified":
                raise ApiProblem(
                    409,
                    "research_not_ready",
                    "A verified filing is required before deep research can run.",
                )
            input_hash = research_input_hash(service, company.ticker)
            existing = service.store.matching_research_run(company, input_hash=input_hash)
            # A malformed terminal artifact projects as failed and must not poison reuse.
            if existing is not None and existing.status != "failed":
                return existing
            run_id = uuid.uuid4().hex
            queued = service.store.begin_research_run(
                company, run_id=run_id, input_hash=input_hash,
                now=datetime.now(UTC).isoformat(),
            )
            model = settings.model
            api_key = environment_api_key(model)
            skeptic_model = _trimmed(os.environ.get("FINWATCH_SKEPTIC_MODEL")) or model

        def work(research_run_id: str, _registry: JobRegistry) -> bool:
            connection = operational_connection()
            repo = Repo(connection)
            scoped = ProductService(repo, user_id=principal.user_id)
            try:
                if not scoped.store.set_research_running(research_run_id):
                    raise ResearchHarnessError("research_state_invalid")
                generator = LiteLLMClient(model, api_key=api_key)
                skeptic = (
                    generator
                    if skeptic_model == model
                    else LiteLLMClient(
                        skeptic_model, api_key=environment_api_key(skeptic_model)
                    )
                )
                result = CompanyResearchHarness(scoped, generator, skeptic).run(
                    company.ticker
                )
                if not scoped.store.finish_research_run(
                    research_run_id,
                    status=result.status,
                    report_json=result.report.model_dump_json(),
                    trace_json=result.trace.model_dump_json(),
                    now=datetime.now(UTC).isoformat(),
                ):
                    raise ResearchHarnessError("research_state_invalid")
                return result.status == "partial"
            except Exception:  # noqa: BLE001 - no provider/exception text is persisted
                scoped.store.fail_research_run(
                    research_run_id, now=datetime.now(UTC).isoformat()
                )
                raise
            finally:
                connection.close()

        try:
            app.state.jobs.start(
                "research", work, owner_id=principal.user_id, job_id=run_id
            )
        except JobConflictError as exc:
            with repo_context() as repo:
                ProductService(repo, user_id=principal.user_id).store.fail_research_run(
                    run_id, now=datetime.now(UTC).isoformat()
                )
            raise ApiProblem(
                409, "job_conflict", "Another background job is already running."
            ) from exc
        capture(principal, "deep_research_started", surface="company", state="queued")
        return queued

    @app.get("/api/research-runs/{run_id}")
    def company_research_run(
        request: Request,
        run_id: str = PathParam(pattern=_JOB_ID_PATTERN, max_length=32),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            result = ProductService(
                repo, user_id=principal.user_id
            ).store.research_run(run_id)
            if result is None:
                raise ApiProblem(404, "research_run_not_found", "Research run not found.")
            return result

    @app.get("/api/companies/{ticker}/risks")
    def company_risks(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            result = ProductService(repo, user_id=principal.user_id).risk_radar(ticker)
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return {"ticker": ticker.upper(), "lenses": result}

    @app.get("/api/companies/{ticker}/profile")
    def company_profile(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            result = ProductService(repo, user_id=principal.user_id).profile(ticker)
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return result

    @app.put("/api/companies/{ticker}/profile")
    def update_company_profile(
        request: Request,
        payload: ProfileUpdate,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            service = ProductService(repo, user_id=principal.user_id)
            profile = service.profile(ticker)
            if profile is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            peer_ciks = []
            for peer_ticker in payload.peer_tickers:
                peer = repo.get_company_by_ticker(peer_ticker)
                if peer is None:
                    raise ApiProblem(422, "peer_not_found", "A selected peer is unknown.")
                if peer.cik != profile.cik and peer.cik not in peer_ciks:
                    peer_ciks.append(peer.cik)
            saved = service.save_profile(
                ticker,
                profile.model_copy(
                    update={
                        "monitoring_enabled": payload.monitoring_enabled,
                        "notification_level": payload.notification_level,
                        "peer_ciks": peer_ciks,
                    }
                ),
            )
            return saved

    @app.put("/api/companies/{ticker}/thesis")
    def update_company_thesis(
        request: Request,
        payload: ThesisUpdate,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            service = ProductService(repo, user_id=principal.user_id)
            profile = service.profile(ticker)
            if profile is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            saved = service.save_profile(
                ticker, profile.model_copy(update={"thesis": payload.thesis})
            ).thesis
            capture(principal, "thesis_saved", surface="company", state="saved")
            return saved

    @app.post("/api/companies/{ticker}/thesis/draft")
    def draft_company_thesis(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            thesis = ProductService(repo, user_id=principal.user_id).draft_thesis(ticker)
            if thesis is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return thesis

    @app.get("/api/companies/{ticker}/peers")
    def company_peers(
        request: Request,
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        principal = principal_for(request)
        with repo_context() as repo:
            result = ProductService(repo, user_id=principal.user_id).peers(ticker)
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return {"ticker": ticker.upper(), "peers": result}

    @app.get("/api/alerts")
    def alerts(request: Request):
        principal = principal_for(request)
        with repo_context() as repo:
            events = ProductService(repo, user_id=principal.user_id).list_events()
        capture(
            principal,
            "alert_opened",
            surface="alerts",
            state="empty" if not events else "populated",
        )
        return {"events": events}

    @app.put("/api/alerts/{event_id}/read", status_code=204)
    def mark_alert_read(request: Request, event_id: int = PathParam(ge=1)):
        principal = principal_for(request)
        with repo_context() as repo:
            if not ProductService(repo, user_id=principal.user_id).mark_event_read(event_id):
                raise ApiProblem(404, "alert_not_found", "Alert not found.")
        return Response(status_code=204)

    @app.post("/api/billing/checkout")
    def billing_checkout(request: Request):
        from finwatch.product.providers import ProviderError, StripeClient

        principal = principal_for(request)
        secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        price_id = os.environ.get("STRIPE_PRICE_ID", "").strip()
        if not secret or not price_id:
            raise ApiProblem(503, "billing_unavailable", "Billing is not configured.")
        with repo_context() as repo:
            user = repo.get_user(principal.user_id)
            if user is None or user.email.endswith(".invalid"):
                raise ApiProblem(409, "email_required", "Sign in before starting checkout.")
            store = ProductService(repo, user_id=principal.user_id).store
            account = store.billing_account()
            base = str(request.base_url).rstrip("/")
            try:
                url = StripeClient(secret).checkout(
                    user_id=principal.user_id,
                    email=user.email,
                    customer_id=account.get("stripe_customer_id") if account else None,
                    price_id=price_id,
                    success_url=f"{base}/research?subscription=active",
                    cancel_url=f"{base}/settings",
                )
            except ProviderError:
                raise ApiProblem(
                    503, "billing_provider_failed", "Billing could not be started."
                ) from None
            capture(principal, "checkout_started", surface="settings", state="created")
            return {"url": url}

    @app.post("/api/billing/portal")
    def billing_portal(request: Request):
        from finwatch.product.providers import ProviderError, StripeClient

        principal = principal_for(request)
        secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if not secret:
            raise ApiProblem(503, "billing_unavailable", "Billing is not configured.")
        with repo_context() as repo:
            account = ProductService(
                repo, user_id=principal.user_id
            ).store.billing_account()
            if not account or not account.get("stripe_customer_id"):
                raise ApiProblem(409, "subscription_not_found", "No subscription was found.")
            try:
                url = StripeClient(secret).portal(
                    customer_id=account["stripe_customer_id"],
                    return_url=f"{str(request.base_url).rstrip('/')}/settings",
                )
            except ProviderError:
                raise ApiProblem(
                    503, "billing_provider_failed", "Billing could not be opened."
                ) from None
            return {"url": url}

    @app.post("/api/webhooks/stripe", status_code=204)
    async def stripe_webhook(request: Request):
        from finwatch.product.providers import ProviderError, verify_stripe_signature
        from finwatch.product.store import ProductStore

        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise ApiProblem(503, "billing_unavailable", "Billing is not configured.")
        raw = await request.body()
        try:
            event = verify_stripe_signature(
                raw, request.headers.get("Stripe-Signature", ""), secret
            )
        except ProviderError:
            raise ApiProblem(400, "invalid_webhook", "Webhook verification failed.") from None
        event_type = event.get("type")
        obj = event.get("data", {}).get("object", {})
        if not isinstance(obj, dict):
            return Response(status_code=204)
        with repo_context() as repo:
            user_id = obj.get("client_reference_id")
            customer_id = obj.get("customer")
            if not isinstance(user_id, str) and isinstance(customer_id, str):
                row = repo.conn.execute(
                    "SELECT user_id FROM billing_accounts WHERE stripe_customer_id = ?",
                    (customer_id,),
                ).fetchone()
                user_id = row["user_id"] if row else None
            if not isinstance(user_id, str) or repo.get_user(user_id) is None:
                return Response(status_code=204)
            subscription_id = obj.get("subscription") or (
                obj.get("id") if str(event_type).startswith("customer.subscription.") else None
            )
            raw_status = obj.get("status")
            status = raw_status if raw_status in {
                "active", "trialing", "past_due", "unpaid", "canceled", "incomplete"
            } else "active" if event_type == "checkout.session.completed" else "free"
            price = None
            items = obj.get("items", {}).get("data", [])
            if isinstance(items, list) and items and isinstance(items[0], dict):
                price = items[0].get("price", {}).get("id")
            ProductStore(repo, user_id).save_billing(
                customer_id=customer_id if isinstance(customer_id, str) else None,
                subscription_id=(
                    subscription_id if isinstance(subscription_id, str) else None
                ),
                status=status,
                price_id=price if isinstance(price, str) else None,
                updated_at=datetime.now(UTC).isoformat(),
            )
        return Response(status_code=204)

    @app.get("/api/settings")
    def get_settings(request: Request):
        principal = principal_for(request)
        with repo_context() as repo:
            return settings_payload(repo, principal)

    @app.put("/api/settings")
    def update_settings(request: Request, payload: SettingsUpdate):
        principal = principal_for(request)
        with repo_context() as repo:
            if "sec_user_agent" in payload.model_fields_set:
                if remote:
                    raise ApiProblem(
                        422,
                        "operator_setting",
                        "The SEC identity is configured by the server operator.",
                    )
                user_agent = _trimmed(payload.sec_user_agent)
                if user_agent is None or "@" not in user_agent:
                    raise ApiProblem(
                        422,
                        "invalid_user_agent",
                        "Enter an SEC User-Agent containing a contact email.",
                    )
                repo.set_setting(SETTING_USER_AGENT, user_agent)
            if payload.period is not None:
                repo.set_user_period(principal.user_id, payload.period)
            return settings_payload(repo, principal)

    def sync_work(user_id: str, ticker: str | None):
        def work(job_id: str, registry: JobRegistry) -> bool:
            with repo_context() as repo:
                settings = resolve_settings(
                    repo,
                    user_id=user_id,
                    remote=remote,
                )
            if not settings.sec_user_agent:
                raise RuntimeError("SEC User-Agent is not configured.")
            config = Config(sec_user_agent=settings.sec_user_agent, db_path=app.state.db_path)
            connection, service = build_service(config, conn=operational_connection())
            partial = False
            try:
                ciks = service.repo.list_tracked_ciks(user_id)
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
                    metrics_failed = False
                    try:
                        _compute_synced_metrics(service, cik, as_of=date.today().isoformat())
                    except Exception:  # noqa: BLE001 - preserve successful ingest work
                        partial = True
                        metrics_failed = True
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=key,
                            state="failed" if result.error or metrics_failed else "completed",
                            message="",
                        ),
                    )
            finally:
                service.edgar.close()
                connection.close()
            return partial

        return work

    def analysis_work(
        user_id: str,
        sec_user_agent: str,
        model: str,
        api_key: str | None,
        ticker: str | None,
        form_type: str | None,
    ):
        def work(job_id: str, registry: JobRegistry) -> bool:
            from pathlib import Path as FilePath

            from finwatch.db import Repo as DatabaseRepo
            from finwatch.ingest import EdgarClient
            from finwatch.llm.router import LiteLLMClient
            from finwatch.pipeline.run import (
                build_orchestrator,
                newest_filing_to_analyze,
                process_filing,
            )
            from finwatch.preprocess.forms import ANALYZABLE_FORMS, base_form

            connection = operational_connection()
            repo = DatabaseRepo(connection)
            tracked_ciks = set(repo.list_tracked_ciks(user_id))
            cik = None
            if ticker:
                company = repo.get_company_by_ticker(ticker)
                if not company or company.cik not in tracked_ciks:
                    connection.close()
                    raise RuntimeError(f"{ticker.upper()} is not tracked.")
                cik = company.cik
            cache = (
                FilePath(app.state.db_path).parent / "cache"
                if app.state.db_path != ":memory:"
                else None
            )
            edgar = EdgarClient(sec_user_agent, cache_dir=cache)
            llm = LiteLLMClient(model, api_key=api_key)
            skeptic_model = os.environ.get("FINWATCH_SKEPTIC_MODEL", "").strip() or model
            if skeptic_model.split("/", 1)[0] != model.split("/", 1)[0]:
                raise RuntimeError("Generator and Skeptic must use the same configured provider.")
            skeptic = (
                LiteLLMClient(skeptic_model, api_key=api_key) if skeptic_model != model else llm
            )
            orchestrator = build_orchestrator(
                repo,
                llm=llm,
                skeptic_llm=skeptic,
                companyfacts_provider=lambda selected_cik: edgar.companyfacts(selected_cik),
                model=model,
                skeptic_model=skeptic_model,
            )
            partial = False
            try:
                if cik is not None:
                    filing = newest_filing_to_analyze(
                        repo,
                        cik,
                        form_type=form_type,
                        allow_reanalysis=True,
                    )
                else:
                    candidates = [
                        candidate
                        for selected_cik in tracked_ciks
                        if (
                            candidate := newest_filing_to_analyze(
                                repo, selected_cik, form_type=form_type
                            )
                        )
                        is not None
                    ]
                    filing = (
                        max(
                            candidates,
                            key=lambda row: (row.filed_at or "", row.accession_number),
                        )
                        if candidates
                        else None
                    )
                if filing is None:
                    scope_ciks = [cik] if cik is not None else sorted(tracked_ciks)
                    synced = [
                        candidate
                        for scope_cik in scope_ciks
                        for candidate in repo.list_filings(scope_cik)
                        if base_form(candidate.form_type) in ANALYZABLE_FORMS
                    ]
                    selected_form = base_form(form_type) if form_type else None
                    if not synced:
                        reason = "no_filings_synced"
                    elif selected_form is not None and not any(
                        base_form(candidate.form_type) == selected_form for candidate in synced
                    ):
                        reason = "form_not_synced"
                    else:
                        reason = "newest_already_analyzed"
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=ticker.upper() if ticker else "portfolio",
                            state="completed",
                            reason=reason,
                        ),
                    )
                else:
                    company = repo.get_company(filing.cik)
                    filing_key = (
                        f"{company.ticker if company else filing.cik} {filing.accession_number}"
                    )

                    def progress(stage, state, _message, diagnostics, key=filing_key):
                        # Carry only the typed failure reason; the registry re-checks it
                        # against its own allowlist before any of it reaches a browser.
                        reason = (
                            diagnostics.get("reason")
                            if isinstance(diagnostics, dict)
                            else None
                        )
                        registry.upsert_item(
                            job_id,
                            JobItem(
                                key=f"{key}:{stage}",
                                state=state,
                                message="",
                                stage=stage,
                                reason=reason if isinstance(reason, str) else None,
                            ),
                        )

                    def fetch(url):
                        return edgar.fetch_primary_doc(url).decode("utf-8", "replace")

                    result = process_filing(
                        orchestrator,
                        repo,
                        filing,
                        fetch_html=fetch,
                        on_stage=progress,
                    )
                    partial = partial or not result.ok or result.withheld
                    registry.add_item(
                        job_id,
                        JobItem(
                            key=f"{result.ticker} {result.accession}",
                            state="completed" if result.ok and not result.withheld else "failed",
                            message="",
                            verdict=result.verdict,
                        ),
                    )
            finally:
                edgar.close()
                connection.close()
            return partial

        return work

    @app.post("/api/jobs/sync", status_code=202)
    def start_sync(request: Request, payload: JobRequest):
        principal = principal_for(request)
        with repo_context() as repo:
            ticker = tracked_job_ticker(repo, principal.user_id, payload.ticker)
        try:
            return app.state.jobs.start(
                "sync",
                sync_work(principal.user_id, ticker),
                owner_id=principal.user_id,
            )
        except JobConflictError as exc:
            raise ApiProblem(409, "job_conflict", str(exc)) from exc

    @app.post("/api/jobs/analyze", status_code=202)
    def start_analysis(request: Request, payload: JobRequest):
        principal = principal_for(request)
        with repo_context() as repo:
            ticker = tracked_job_ticker(repo, principal.user_id, payload.ticker)
            settings = resolve_settings(
                repo,
                user_id=principal.user_id,
                remote=remote,
            )
        if not settings.sec_user_agent:
            raise ApiProblem(
                409, "missing_user_agent", "Configure the SEC User-Agent first."
            )
        run_api_key = environment_api_key(settings.model)
        if not settings.model or not run_api_key:
            raise ApiProblem(
                409, "model_not_configured", "Analysis is temporarily unavailable."
            )
        try:
            return app.state.jobs.start(
                "analysis",
                analysis_work(
                    principal.user_id,
                    settings.sec_user_agent or "",
                    settings.model,
                    run_api_key,
                    ticker,
                    payload.form_type,
                ),
                owner_id=principal.user_id,
            )
        except JobConflictError as exc:
            raise ApiProblem(409, "job_conflict", str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def get_job(
        request: Request,
        job_id: str = PathParam(pattern=_JOB_ID_PATTERN),
    ):
        principal = principal_for(request)
        job = app.state.jobs.get(job_id, owner_id=principal.user_id)
        if job is None:
            raise ApiProblem(404, "job_not_found", "Job not found.")
        return job

    # ---- public SEC showcase -----------------------------------------------
    # Read-only projections of a reserved showcase owner, reachable without an account.
    # A completed operator refresh reads cached shared SEC artifacts from the production
    # database; otherwise the zero-key bundled fixture is used. These deliberately do NOT
    # call principal_for, and no anonymous request can trigger EDGAR or model work.

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}bootstrap")
    def public_sample_bootstrap():
        """A fixed literal, never settings_payload().

        settings_payload reports whether the *operator* has a provider key configured
        and which model is selected. That is not public information, and an anonymous
        caller has no session for it to describe.
        """
        with repo_context(True) as repo:
            refreshed_at = public_showcase_refreshed_at(repo)
            return {
                "setup_required": False,
                "sec_user_agent": "",
                "account_email": None,
                "period": "90d",
                "model": "",
                "provider": None,
                "api_key_configured": False,
                "analysis_configured": False,
                "billing_configured": False,
                "billing_status": "free",
                "showcase_source": "sec_cache" if refreshed_at else "bundled_fixture",
                "showcase_updated_at": refreshed_at,
            }

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}brief")
    def public_sample_brief():
        with repo_context(True) as repo:
            since = _since_for_period("1y") if public_showcase_refreshed_at(repo) else DEMO_SINCE
            return PresentationService(repo, user_id=PUBLIC_SHOWCASE_USER_ID).brief(
                since=since, sample_data=True
            )

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}companies")
    def public_sample_companies():
        with repo_context(True) as repo:
            return PresentationService(repo, user_id=PUBLIC_SHOWCASE_USER_ID).companies()

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}alerts")
    def public_sample_alerts():
        with repo_context(True) as repo:
            return {
                "events": ProductService(
                    repo, user_id=PUBLIC_SHOWCASE_USER_ID
                ).list_events()
            }

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}companies/{{ticker}}/research")
    def public_sample_company_research(
        ticker: str = PathParam(pattern=_TICKER_PATTERN, max_length=15),
    ):
        with repo_context(True) as repo:
            result = ProductService(
                repo, user_id=PUBLIC_SHOWCASE_USER_ID
            ).before_you_buy(ticker)
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return result

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}filings/{{accession}}")
    def public_sample_filing(accession: str = PathParam(pattern=_ACCESSION_PATTERN)):
        with repo_context(True) as repo:
            result = PresentationService(repo, user_id=PUBLIC_SHOWCASE_USER_ID).filing(
                accession, sample_data=True
            )
            if result is None:
                raise ApiProblem(404, "filing_not_found", "Filing not found.")
            return result

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}filings/{{accession}}/certificate")
    def public_sample_certificate(
        accession: str = PathParam(pattern=_ACCESSION_PATTERN),
    ):
        with repo_context(True) as repo:
            result = PresentationService(repo, user_id=PUBLIC_SHOWCASE_USER_ID).certificate(
                accession
            )
            if result is None:
                raise ApiProblem(404, "certificate_not_found", "Certificate not found.")
            return result

    @app.get(f"{PUBLIC_SAMPLE_PREFIX}companies/{{ticker}}/metrics")
    def public_sample_metrics(
        ticker: str = PathParam(pattern=_TICKER_PATTERN),
        as_of: str | None = None,
    ):
        with repo_context(True) as repo:
            result = PresentationService(repo, user_id=PUBLIC_SHOWCASE_USER_ID).metrics(
                ticker, as_of=as_of
            )
            if result is None:
                raise ApiProblem(404, "company_not_found", "Company not found.")
            return result

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
