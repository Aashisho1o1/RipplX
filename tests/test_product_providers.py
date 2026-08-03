from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from finwatch.product.providers import (
    ProviderError,
    SafeAnalytics,
    StripeClient,
    verify_stripe_signature,
)


def test_stripe_checkout_sends_only_subscription_fields_and_returns_https_url():
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = request.content.decode()
        captured["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"url": "https://checkout.stripe.com/c/pay_test"})

    client = StripeClient(
        "sk_test_safe", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    url = client.checkout(
        user_id="u1",
        email="person@example.com",
        price_id="price_founder",
        success_url="https://app.example/research?paid=1",
        cancel_url="https://app.example/settings",
    )
    assert url.startswith("https://checkout.stripe.com/")
    assert captured["auth"] == "Bearer sk_test_safe"
    assert "mode=subscription" in captured["body"]
    assert "holdings" not in captured["body"]


def test_stripe_webhook_requires_current_raw_body_signature():
    body = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
    timestamp = 1000
    signature = hmac.new(
        b"whsec_test", str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    event = verify_stripe_signature(body, f"t={timestamp},v1={signature}", "whsec_test", now=1100)
    assert event["id"] == "evt_1"
    with pytest.raises(ProviderError):
        verify_stripe_signature(
            body + b" ", f"t={timestamp},v1={signature}", "whsec_test", now=1100
        )
    with pytest.raises(ProviderError):
        verify_stripe_signature(body, f"t={timestamp},v1={signature}", "whsec_test", now=2000)


def test_posthog_payload_contract_rejects_financial_and_identity_properties():
    analytics = SafeAnalytics(
        "phc_test",
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200))),
    )
    assert analytics.capture("user-1", "research_opened", {"surface": "company"})
    for properties in (
        {"ticker": "AAPL"},
        {"holdings": "secret"},
        {"thesis": "private"},
        {"valuation_input": "120"},
    ):
        with pytest.raises(ValueError):
            analytics.capture("user-1", "research_opened", properties)
