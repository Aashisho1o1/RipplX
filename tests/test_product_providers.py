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


def test_stripe_subscription_list_validates_the_customer_and_response():
    import httpx
    import pytest

    from finwatch.product.providers import ProviderError, StripeClient

    requests = []
    replies = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=replies.pop(0))

    client = StripeClient("sk_test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    replies.append({"data": [{"id": "sub_1", "customer": "cus_123"}], "has_more": False})
    assert client.customer_subscriptions("cus_123") == [{"id": "sub_1", "customer": "cus_123"}]
    url = requests[0].url
    assert (url.host, url.path) == ("api.stripe.com", "/v1/subscriptions")
    assert dict(url.params) == {"customer": "cus_123", "status": "all", "limit": "100"}
    assert requests[0].headers["Authorization"] == "Bearer sk_test"

    for bad in ("cus_../x", "sub_123", "", "cus_1&status=active"):
        with pytest.raises(ProviderError):
            client.customer_subscriptions(bad)
    assert len(requests) == 1

    # A row for another customer, a truncated list, or a malformed body fails closed.
    for reply in (
        {"data": [{"id": "sub_1", "customer": "cus_other"}], "has_more": False},
        {"data": [], "has_more": True},
        {"object": "list"},
    ):
        replies.append(reply)
        with pytest.raises(ProviderError):
            client.customer_subscriptions("cus_123")


def test_stripe_create_customer_sends_only_email_and_account_reference():
    import httpx
    import pytest

    from finwatch.product.providers import ProviderError, StripeClient

    seen = []
    replies = [{"id": "cus_abc"}, {"id": "sub_not_a_customer"}]

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=replies.pop(0))

    client = StripeClient("sk_test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.create_customer(email="a@example.com", user_id="u1") == "cus_abc"
    assert str(seen[0].url) == "https://api.stripe.com/v1/customers"
    assert dict(httpx.QueryParams(seen[0].content.decode())) == {
        "email": "a@example.com", "metadata[ripplx_user]": "u1",
    }
    with pytest.raises(ProviderError):
        client.create_customer(email="a@example.com", user_id="u1")
