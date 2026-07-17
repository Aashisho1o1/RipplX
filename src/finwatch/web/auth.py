"""Lean public email-code authentication primitives for the hosted web app."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx
from itsdangerous import BadData, URLSafeSerializer
from pydantic import BaseModel, ConfigDict, Field, field_validator

AUTH_SECRET_MIN_BYTES = 32
OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
OTP_EMAIL_COOLDOWN_SECONDS = 60
OTP_EMAIL_HOURLY_LIMIT = 5
OTP_GLOBAL_HOURLY_LIMIT = 100
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

SESSION_COOKIE_NAME = "__Host-finwatch_session"
CSRF_COOKIE_NAME = "__Host-finwatch_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

_HOUR_SECONDS = 60 * 60
_RESEND_ENDPOINT = "https://api.resend.com/emails"
_EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+$")
_EMAIL_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_CHALLENGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_SIGNED_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{20,2048}$")


class InvalidEmailError(ValueError):
    """The supplied address is outside the prototype's supported email shape."""


class EmailDeliveryError(RuntimeError):
    """Safe public error for an email provider failure."""

    def __init__(self) -> None:
        super().__init__("Sign-in code could not be sent. Please try again.")


class OtpRateLimitError(RuntimeError):
    """A sign-in code request exceeded one of the small in-memory limits."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__("Please wait before requesting another sign-in code.")


class OtpVerificationError(RuntimeError):
    """Safe public error shared by unknown, wrong, expired, and replayed codes."""

    def __init__(self) -> None:
        super().__init__("The sign-in code is invalid or has expired.")


class InvalidSessionError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The session is invalid or has expired.")


class InvalidCsrfError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The request security token is invalid.")


class RequestCodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        try:
            return normalize_email(value)
        except InvalidEmailError as exc:
            raise ValueError(str(exc)) from None


class VerifyCodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=20, max_length=128, pattern=_CHALLENGE_ID_RE.pattern)
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$", strict=True)


class EmailSender(Protocol):
    def __call__(self, recipient: str, code: str) -> None: ...


@dataclass(frozen=True)
class OtpChallenge:
    challenge_id: str
    expires_at: int


@dataclass
class _StoredChallenge:
    email: str
    code_digest: bytes
    expires_at: float
    attempts: int = 0


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    user_id: str
    expires_at: int


@dataclass(frozen=True)
class IssuedSession:
    token: str
    identity: SessionIdentity


def normalize_email(value: str) -> str:
    """Return one stable ASCII address or reject it without adding a dependency."""
    if not isinstance(value, str):
        raise InvalidEmailError("Enter a valid email address.")
    email = value.strip()
    try:
        email.encode("ascii")
    except UnicodeEncodeError:
        raise InvalidEmailError("Enter a valid email address.") from None
    email = email.lower()
    if len(email) > 254 or email.count("@") != 1:
        raise InvalidEmailError("Enter a valid email address.")
    local, domain = email.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or not _EMAIL_LOCAL_RE.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
    ):
        raise InvalidEmailError("Enter a valid email address.")
    labels = domain.split(".")
    if len(domain) > 253 or len(labels) < 2 or any(
        not _EMAIL_DOMAIN_LABEL_RE.fullmatch(label) for label in labels
    ):
        raise InvalidEmailError("Enter a valid email address.")
    return email


def _secret_bytes(value: str | bytes) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(secret, bytes) or len(secret) < AUTH_SECRET_MIN_BYTES:
        raise RuntimeError(
            f"FINWATCH_AUTH_SECRET must contain at least {AUTH_SECRET_MIN_BYTES} bytes."
        )
    return secret


def _clock_value(clock: Callable[[], float]) -> float:
    now = float(clock())
    if not math.isfinite(now):
        raise RuntimeError("Authentication clock returned an invalid value.")
    return now


def _opaque_id(value: str, *, minimum: int = 1) -> bool:
    return len(value) >= minimum and bool(_OPAQUE_ID_RE.fullmatch(value))


class EmailOtpManager:
    """Thread-safe one-process OTP challenges and deliberately small rate limits."""

    def __init__(
        self,
        *,
        auth_secret: str | bytes,
        email_sender: EmailSender,
        clock: Callable[[], float] = time.time,
        code_factory: Callable[[], str] | None = None,
        challenge_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._secret = _secret_bytes(auth_secret)
        self._email_sender = email_sender
        self._clock = clock
        self._code_factory = code_factory or (lambda: f"{secrets.randbelow(1_000_000):06d}")
        self._challenge_id_factory = challenge_id_factory or (
            lambda: secrets.token_urlsafe(24)
        )
        self._lock = threading.Lock()
        self._challenges: dict[str, _StoredChallenge] = {}
        self._active_by_email: dict[str, str] = {}
        self._email_sends: dict[str, deque[float]] = defaultdict(deque)
        self._global_sends: deque[float] = deque()

    def _digest(self, challenge_id: str, email: str, code: str) -> bytes:
        message = f"ripplx-email-otp-v1\0{challenge_id}\0{email}\0{code}".encode("ascii")
        return hmac.new(self._secret, message, hashlib.sha256).digest()

    def _remove_challenge_locked(self, challenge_id: str) -> None:
        challenge = self._challenges.pop(challenge_id, None)
        if challenge and self._active_by_email.get(challenge.email) == challenge_id:
            del self._active_by_email[challenge.email]

    def _prune_locked(self, now: float) -> None:
        for challenge_id, challenge in list(self._challenges.items()):
            if challenge.expires_at <= now:
                self._remove_challenge_locked(challenge_id)

        cutoff = now - _HOUR_SECONDS
        while self._global_sends and self._global_sends[0] <= cutoff:
            self._global_sends.popleft()
        for email, sends in list(self._email_sends.items()):
            while sends and sends[0] <= cutoff:
                sends.popleft()
            if not sends:
                del self._email_sends[email]

    @staticmethod
    def _retry_after(until: float, now: float) -> int:
        return max(1, math.ceil(until - now))

    def _check_rate_limit_locked(self, email: str, now: float) -> None:
        sends = self._email_sends.get(email)
        if sends and now - sends[-1] < OTP_EMAIL_COOLDOWN_SECONDS:
            raise OtpRateLimitError(
                self._retry_after(sends[-1] + OTP_EMAIL_COOLDOWN_SECONDS, now)
            )
        if sends and len(sends) >= OTP_EMAIL_HOURLY_LIMIT:
            raise OtpRateLimitError(self._retry_after(sends[0] + _HOUR_SECONDS, now))
        if len(self._global_sends) >= OTP_GLOBAL_HOURLY_LIMIT:
            raise OtpRateLimitError(
                self._retry_after(self._global_sends[0] + _HOUR_SECONDS, now)
            )

    def _new_challenge_id_locked(self) -> str:
        for _ in range(4):
            challenge_id = self._challenge_id_factory()
            if (
                isinstance(challenge_id, str)
                and _CHALLENGE_ID_RE.fullmatch(challenge_id)
                and challenge_id not in self._challenges
            ):
                return challenge_id
        raise RuntimeError("Could not allocate an authentication challenge identifier.")

    def request_code(self, email: str) -> OtpChallenge:
        normalized = normalize_email(email)
        with self._lock:
            now = _clock_value(self._clock)
            self._prune_locked(now)
            self._check_rate_limit_locked(normalized, now)
            challenge_id = self._new_challenge_id_locked()
            code = self._code_factory()
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                raise RuntimeError("Authentication code generator returned an invalid value.")

            # Keep the lock while sending. This prototype has one process and a strict
            # global send ceiling; serialization makes the rate-limit decision atomic.
            try:
                self._email_sender(normalized, code)
            except Exception:  # noqa: BLE001 - provider details are never public
                raise EmailDeliveryError() from None

            previous = self._active_by_email.get(normalized)
            if previous:
                self._remove_challenge_locked(previous)
            expires_at = now + OTP_TTL_SECONDS
            self._challenges[challenge_id] = _StoredChallenge(
                email=normalized,
                code_digest=self._digest(challenge_id, normalized, code),
                expires_at=expires_at,
            )
            self._active_by_email[normalized] = challenge_id
            self._email_sends[normalized].append(now)
            self._global_sends.append(now)
            return OtpChallenge(challenge_id=challenge_id, expires_at=int(expires_at))

    def verify_code(self, challenge_id: str, code: str) -> str:
        with self._lock:
            now = _clock_value(self._clock)
            self._prune_locked(now)
            if not isinstance(challenge_id, str) or not _CHALLENGE_ID_RE.fullmatch(
                challenge_id
            ):
                raise OtpVerificationError()
            challenge = self._challenges.get(challenge_id)
            if challenge is None:
                raise OtpVerificationError()

            challenge.attempts += 1
            candidate = code if isinstance(code, str) else ""
            well_formed = bool(re.fullmatch(r"[0-9]{6}", candidate))
            expected = self._digest(
                challenge_id,
                challenge.email,
                candidate if well_formed else "000000",
            )
            valid = well_formed and hmac.compare_digest(
                expected, challenge.code_digest
            )
            if valid:
                email = challenge.email
                self._remove_challenge_locked(challenge_id)
                return email
            if challenge.attempts >= OTP_MAX_ATTEMPTS:
                self._remove_challenge_locked(challenge_id)
            raise OtpVerificationError()


class ResendEmailSender:
    """Minimal Resend REST client that never exposes response or credential text."""

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._from_address = from_address.strip()
        if not self._api_key:
            raise RuntimeError("RESEND_API_KEY is required for hosted email sign-in.")
        if (
            not self._from_address
            or len(self._from_address) > 320
            or "\r" in self._from_address
            or "\n" in self._from_address
        ):
            raise RuntimeError("FINWATCH_EMAIL_FROM must be a valid email sender value.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._client = client
        self._timeout_seconds = timeout_seconds

    def __call__(self, recipient: str, code: str) -> None:
        recipient = normalize_email(recipient)
        if not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("A six-digit sign-in code is required.")
        payload = {
            "from": self._from_address,
            "to": [recipient],
            "subject": "Your RipplX sign-in code",
            "text": (
                f"Your RipplX sign-in code is {code}.\n\n"
                "It expires in 10 minutes. If you did not request it, ignore this email."
            ),
        }
        try:
            if self._client is None:
                response = httpx.post(
                    _RESEND_ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                )
            else:
                response = self._client.post(
                    _RESEND_ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                )
            if not response.is_success:
                raise EmailDeliveryError()
        except EmailDeliveryError:
            raise
        except httpx.HTTPError:
            raise EmailDeliveryError() from None


class SessionCodec:
    """Sign and validate the minimal readable session-cookie payload."""

    def __init__(
        self,
        auth_secret: str | bytes,
        *,
        clock: Callable[[], float] = time.time,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        secret = _secret_bytes(auth_secret)
        self._serializer = URLSafeSerializer(
            secret,
            salt="ripplx-session-v1",
            signer_kwargs={"digest_method": hashlib.sha256},
        )
        self._clock = clock
        self._session_id_factory = session_id_factory or (lambda: secrets.token_urlsafe(32))

    def issue(self, user_id: str) -> IssuedSession:
        if not isinstance(user_id, str) or not _opaque_id(user_id):
            raise ValueError("user_id must be an opaque ASCII identifier")
        session_id = self._session_id_factory()
        if not isinstance(session_id, str) or not _opaque_id(session_id, minimum=20):
            raise RuntimeError("Session identifier generator returned an invalid value.")
        expires_at = int(_clock_value(self._clock)) + SESSION_TTL_SECONDS
        identity = SessionIdentity(
            session_id=session_id,
            user_id=user_id,
            expires_at=expires_at,
        )
        token = self._serializer.dumps(
            {"sid": session_id, "uid": user_id, "exp": expires_at}
        )
        return IssuedSession(token=token, identity=identity)

    def load(self, token: str) -> SessionIdentity:
        if not isinstance(token, str) or not _SIGNED_TOKEN_RE.fullmatch(token):
            raise InvalidSessionError()
        try:
            payload = self._serializer.loads(token)
        except BadData:
            raise InvalidSessionError() from None
        if not isinstance(payload, dict) or set(payload) != {"sid", "uid", "exp"}:
            raise InvalidSessionError()
        session_id = payload.get("sid")
        user_id = payload.get("uid")
        expires_at = payload.get("exp")
        now = _clock_value(self._clock)
        if (
            not isinstance(session_id, str)
            or not _opaque_id(session_id, minimum=20)
            or not isinstance(user_id, str)
            or not _opaque_id(user_id)
            or type(expires_at) is not int
            or expires_at <= now
            or expires_at > int(now) + SESSION_TTL_SECONDS
        ):
            raise InvalidSessionError()
        return SessionIdentity(
            session_id=session_id,
            user_id=user_id,
            expires_at=expires_at,
        )


class CsrfCodec:
    """Issue a signed double-submit token bound to exactly one session."""

    def __init__(
        self,
        auth_secret: str | bytes,
        *,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        secret = _secret_bytes(auth_secret)
        self._serializer = URLSafeSerializer(
            secret,
            salt="ripplx-csrf-v1",
            signer_kwargs={"digest_method": hashlib.sha256},
        )
        self._clock = clock
        self._nonce_factory = nonce_factory or (lambda: secrets.token_urlsafe(32))

    def issue(self, session: SessionIdentity) -> str:
        now = _clock_value(self._clock)
        if (
            not isinstance(session.session_id, str)
            or not _opaque_id(session.session_id, minimum=20)
            or not isinstance(session.user_id, str)
            or not _opaque_id(session.user_id)
            or type(session.expires_at) is not int
            or session.expires_at <= now
            or session.expires_at > int(now) + SESSION_TTL_SECONDS
        ):
            raise InvalidSessionError()
        nonce = self._nonce_factory()
        if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
            raise RuntimeError("CSRF nonce generator returned an invalid value.")
        return self._serializer.dumps(
            {"sid": session.session_id, "nonce": nonce, "exp": session.expires_at}
        )

    def validate(
        self,
        *,
        cookie_token: str | None,
        header_token: str | None,
        session: SessionIdentity,
    ) -> None:
        now = _clock_value(self._clock)
        if (
            not isinstance(session.session_id, str)
            or not _opaque_id(session.session_id, minimum=20)
            or type(session.expires_at) is not int
            or session.expires_at <= now
            or session.expires_at > int(now) + SESSION_TTL_SECONDS
            or not isinstance(cookie_token, str)
            or not isinstance(header_token, str)
            or not _SIGNED_TOKEN_RE.fullmatch(cookie_token)
            or not _SIGNED_TOKEN_RE.fullmatch(header_token)
            or not hmac.compare_digest(cookie_token, header_token)
        ):
            raise InvalidCsrfError()
        try:
            payload = self._serializer.loads(cookie_token)
        except BadData:
            raise InvalidCsrfError() from None
        if not isinstance(payload, dict) or set(payload) != {"sid", "nonce", "exp"}:
            raise InvalidCsrfError()
        session_id = payload.get("sid")
        nonce = payload.get("nonce")
        expires_at = payload.get("exp")
        if (
            not isinstance(session_id, str)
            or not _opaque_id(session_id, minimum=20)
            or not hmac.compare_digest(session_id, session.session_id)
            or not isinstance(nonce, str)
            or not _NONCE_RE.fullmatch(nonce)
            or type(expires_at) is not int
            or expires_at != session.expires_at
            or expires_at <= now
        ):
            raise InvalidCsrfError()
