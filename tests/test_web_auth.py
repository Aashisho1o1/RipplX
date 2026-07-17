"""Focused tests for public email-code and signed-session primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import pytest
from pydantic import ValidationError

from finwatch.web.auth import (
    OTP_GLOBAL_HOURLY_LIMIT,
    OTP_MAX_ATTEMPTS,
    OTP_TTL_SECONDS,
    SESSION_TTL_SECONDS,
    CsrfCodec,
    EmailDeliveryError,
    EmailOtpManager,
    InvalidCsrfError,
    InvalidEmailError,
    InvalidSessionError,
    OtpRateLimitError,
    OtpVerificationError,
    RequestCodeBody,
    ResendEmailSender,
    SessionCodec,
    VerifyCodeBody,
    normalize_email,
)

SECRET = "test-auth-secret-that-is-longer-than-thirty-two-characters"


@dataclass
class FakeClock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, recipient: str, code: str) -> None:
        self.sent.append((recipient, code))


def manager_for(
    sender,
    clock: FakeClock,
    *,
    codes=None,
) -> EmailOtpManager:
    code_values = iter(codes or ["123456"] * 200)
    return EmailOtpManager(
        auth_secret=SECRET,
        email_sender=sender,
        clock=clock,
        code_factory=lambda: next(code_values),
    )


@pytest.mark.parametrize(
    "value",
    [
        "missing-at.example.com",
        "two@@example.com",
        ".leading@example.com",
        "double..dot@example.com",
        "person@localhost",
        "person@-example.com",
        "person@example.com/path",
        "tést@example.com",
    ],
)
def test_email_normalization_rejects_unsupported_addresses(value):
    with pytest.raises(InvalidEmailError, match="valid email"):
        normalize_email(value)


def test_request_model_normalizes_email_and_write_models_reject_extra_fields():
    assert RequestCodeBody(email="  Person+Test@Example.COM ").email == (
        "person+test@example.com"
    )
    with pytest.raises(ValidationError):
        RequestCodeBody(email="person@example.com", invite="not-supported")
    with pytest.raises(ValidationError):
        VerifyCodeBody(challenge_id="a" * 32, code="123456", email="person@example.com")
    with pytest.raises(ValidationError):
        VerifyCodeBody(challenge_id="a" * 32, code="12345x")


def test_otp_is_normalized_hmac_digest_and_single_use():
    clock = FakeClock()
    sender = RecordingSender()
    manager = manager_for(sender, clock)

    challenge = manager.request_code("  Person@Example.COM ")

    assert sender.sent == [("person@example.com", "123456")]
    assert challenge.expires_at == int(clock.value) + OTP_TTL_SECONDS
    stored = manager._challenges[challenge.challenge_id]
    assert stored.code_digest != b"123456"
    assert "123456" not in repr(stored)
    assert manager.verify_code(challenge.challenge_id, "123456") == "person@example.com"
    with pytest.raises(OtpVerificationError, match="invalid or has expired"):
        manager.verify_code(challenge.challenge_id, "123456")


def test_new_code_replaces_previous_challenge():
    clock = FakeClock()
    manager = manager_for(RecordingSender(), clock, codes=["111111", "222222"])
    first = manager.request_code("person@example.com")
    clock.advance(60)
    second = manager.request_code("person@example.com")

    with pytest.raises(OtpVerificationError):
        manager.verify_code(first.challenge_id, "111111")
    assert manager.verify_code(second.challenge_id, "222222") == "person@example.com"


def test_expired_code_and_five_failed_attempts_are_invalidated():
    clock = FakeClock()
    manager = manager_for(RecordingSender(), clock, codes=["111111", "222222"])
    expired = manager.request_code("expired@example.com")
    clock.advance(OTP_TTL_SECONDS)
    with pytest.raises(OtpVerificationError):
        manager.verify_code(expired.challenge_id, "111111")

    limited = manager.request_code("attempts@example.com")
    for _ in range(OTP_MAX_ATTEMPTS):
        with pytest.raises(OtpVerificationError):
            manager.verify_code(limited.challenge_id, "000000")
    with pytest.raises(OtpVerificationError):
        manager.verify_code(limited.challenge_id, "222222")


def test_correct_code_is_accepted_on_fifth_attempt():
    clock = FakeClock()
    manager = manager_for(RecordingSender(), clock)
    challenge = manager.request_code("person@example.com")
    for _ in range(OTP_MAX_ATTEMPTS - 1):
        with pytest.raises(OtpVerificationError):
            manager.verify_code(challenge.challenge_id, "000000")
    assert manager.verify_code(challenge.challenge_id, "123456") == "person@example.com"


def test_non_ascii_digits_are_a_normal_failed_attempt_not_an_internal_error():
    clock = FakeClock()
    manager = manager_for(RecordingSender(), clock)
    challenge = manager.request_code("person@example.com")

    with pytest.raises(OtpVerificationError):
        manager.verify_code(challenge.challenge_id, "١٢٣٤٥٦")
    assert manager.verify_code(challenge.challenge_id, "123456") == "person@example.com"


def test_per_email_cooldown_and_hourly_limit():
    clock = FakeClock()
    manager = manager_for(RecordingSender(), clock)
    manager.request_code("person@example.com")
    with pytest.raises(OtpRateLimitError) as cooldown:
        manager.request_code("person@example.com")
    assert cooldown.value.retry_after_seconds == 60

    for _ in range(4):
        clock.advance(60)
        manager.request_code("person@example.com")
    clock.advance(60)
    with pytest.raises(OtpRateLimitError) as hourly:
        manager.request_code("person@example.com")
    assert hourly.value.retry_after_seconds == 3_300

    clock.advance(3_300)
    manager.request_code("person@example.com")


def test_global_hourly_limit_is_bounded_and_resets():
    clock = FakeClock()
    manager = manager_for(RecordingSender(), clock)
    for index in range(OTP_GLOBAL_HOURLY_LIMIT):
        manager.request_code(f"person{index}@example.com")
    with pytest.raises(OtpRateLimitError) as limited:
        manager.request_code("one-more@example.com")
    assert limited.value.retry_after_seconds == 3_600

    clock.advance(3_600)
    manager.request_code("one-more@example.com")


def test_failed_delivery_exposes_no_provider_text_and_consumes_no_limit():
    clock = FakeClock()
    calls = 0

    def sometimes_fails(_recipient: str, _code: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider leaked sk-secret-sentinel")

    manager = manager_for(sometimes_fails, clock)
    with pytest.raises(EmailDeliveryError) as failure:
        manager.request_code("person@example.com")
    assert "sentinel" not in str(failure.value)

    # The failed attempt created neither a challenge nor a cooldown reservation.
    challenge = manager.request_code("person@example.com")
    assert manager.verify_code(challenge.challenge_id, "123456") == "person@example.com"


def test_resend_sender_uses_fixed_endpoint_and_minimal_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(202, json={"id": "email-id"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sender = ResendEmailSender(
            api_key="resend-test-key",
            from_address="RipplX <login@example.com>",
            client=client,
        )
        sender("Person@Example.com", "123456")

    assert captured == {
        "url": "https://api.resend.com/emails",
        "authorization": "Bearer resend-test-key",
        "payload": {
            "from": "RipplX <login@example.com>",
            "to": ["person@example.com"],
            "subject": "Your RipplX sign-in code",
            "text": (
                "Your RipplX sign-in code is 123456.\n\n"
                "It expires in 10 minutes. If you did not request it, ignore this email."
            ),
        },
    }


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_resend_http_errors_are_fixed_and_do_not_leak_response(status):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="provider sk-secret-sentinel")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sender = ResendEmailSender(
            api_key="resend-test-key",
            from_address="login@example.com",
            client=client,
        )
        with pytest.raises(EmailDeliveryError) as failure:
            sender("person@example.com", "123456")
    assert str(failure.value) == "Sign-in code could not be sent. Please try again."
    assert "sentinel" not in str(failure.value)


def test_resend_network_error_is_fixed_and_safe():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network sk-secret-sentinel", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sender = ResendEmailSender(
            api_key="resend-test-key",
            from_address="login@example.com",
            client=client,
        )
        with pytest.raises(EmailDeliveryError) as failure:
            sender("person@example.com", "123456")
    assert str(failure.value) == "Sign-in code could not be sent. Please try again."
    assert "sentinel" not in str(failure.value)


def test_session_is_minimal_signed_and_expires_after_thirty_days():
    clock = FakeClock()
    codec = SessionCodec(
        SECRET,
        clock=clock,
        session_id_factory=lambda: "s" * 32,
    )
    issued = codec.issue("u" * 32)

    assert issued.identity.session_id == "s" * 32
    assert issued.identity.user_id == "u" * 32
    assert issued.identity.expires_at == int(clock.value) + SESSION_TTL_SECONDS
    assert set(codec._serializer.loads(issued.token)) == {"sid", "uid", "exp"}
    assert codec.load(issued.token) == issued.identity

    tampered = ("A" if issued.token[0] != "A" else "B") + issued.token[1:]
    with pytest.raises(InvalidSessionError):
        codec.load(tampered)

    clock.advance(SESSION_TTL_SECONDS)
    with pytest.raises(InvalidSessionError):
        codec.load(issued.token)


def test_csrf_requires_matching_double_submit_token_and_originating_session():
    clock = FakeClock()
    sessions = iter(["a" * 32, "b" * 32])
    session_codec = SessionCodec(
        SECRET,
        clock=clock,
        session_id_factory=lambda: next(sessions),
    )
    csrf_codec = CsrfCodec(SECRET, clock=clock, nonce_factory=lambda: "n" * 32)
    first = session_codec.issue("u" * 32).identity
    second = session_codec.issue("v" * 32).identity
    token = csrf_codec.issue(first)

    csrf_codec.validate(cookie_token=token, header_token=token, session=first)
    with pytest.raises(InvalidCsrfError):
        csrf_codec.validate(cookie_token=token, header_token="x" * 32, session=first)
    with pytest.raises(InvalidCsrfError):
        csrf_codec.validate(cookie_token=token, header_token=token, session=second)

    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    with pytest.raises(InvalidCsrfError):
        csrf_codec.validate(cookie_token=tampered, header_token=tampered, session=first)

    clock.advance(SESSION_TTL_SECONDS)
    with pytest.raises(InvalidCsrfError):
        csrf_codec.validate(cookie_token=token, header_token=token, session=first)


def test_session_and_csrf_signing_domains_are_separate():
    clock = FakeClock()
    session_codec = SessionCodec(
        SECRET,
        clock=clock,
        session_id_factory=lambda: "s" * 32,
    )
    csrf_codec = CsrfCodec(SECRET, clock=clock, nonce_factory=lambda: "n" * 32)
    issued = session_codec.issue("u" * 32)
    csrf_token = csrf_codec.issue(issued.identity)

    with pytest.raises(InvalidSessionError):
        session_codec.load(csrf_token)
    with pytest.raises(InvalidCsrfError):
        csrf_codec.validate(
            cookie_token=issued.token,
            header_token=issued.token,
            session=issued.identity,
        )


def test_auth_secret_must_be_at_least_thirty_two_bytes():
    with pytest.raises(RuntimeError, match="at least 32"):
        SessionCodec("too-short")
    with pytest.raises(RuntimeError, match="at least 32"):
        EmailOtpManager(auth_secret="too-short", email_sender=lambda _email, _code: None)
