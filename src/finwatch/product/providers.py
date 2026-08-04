"""Narrow provider clients; failures stay outside the verified research path."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urljoin

import httpx


class ProviderError(RuntimeError):
    """A redacted provider failure safe to map to a generic API response."""


class StripeClient:
    API = "https://api.stripe.com/v1/"

    def __init__(self, secret_key: str, *, client: httpx.Client | None = None) -> None:
        self._key = secret_key.strip()
        if not self._key:
            raise ValueError("Stripe secret key is required")
        self._client = client or httpx.Client(timeout=15, follow_redirects=False)

    def _post(self, path: str, data: dict) -> dict:
        try:
            response = self._client.post(
                urljoin(self.API, path),
                headers={"Authorization": f"Bearer {self._key}"},
                data=data,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("Stripe request failed") from None
        if not isinstance(payload, dict):
            raise ProviderError("Stripe response was invalid")
        return payload

    def checkout(
        self,
        *,
        user_id: str,
        email: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
    ) -> str:
        data = {
            "mode": "subscription",
            "client_reference_id": user_id,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "allow_promotion_codes": "true",
        }
        data["customer" if customer_id else "customer_email"] = customer_id or email
        payload = self._post(
            "checkout/sessions",
            data,
        )
        url = payload.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderError("Stripe did not return a checkout URL")
        return url

    def portal(self, *, customer_id: str, return_url: str) -> str:
        payload = self._post(
            "billing_portal/sessions",
            {
                "customer": customer_id,
                "return_url": return_url,
            },
        )
        url = payload.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderError("Stripe did not return a portal URL")
        return url


def verify_stripe_signature(
    body: bytes,
    header: str,
    secret: str,
    *,
    now: int | None = None,
    tolerance_seconds: int = 300,
) -> dict:
    """Verify Stripe's t=timestamp,v1=HMAC header over the untouched request body."""
    parts = [part.split("=", 1) for part in header.split(",") if "=" in part]
    timestamps = [value for key, value in parts if key == "t"]
    signatures = [value for key, value in parts if key == "v1"]
    try:
        timestamp = int(timestamps[0])
    except (IndexError, ValueError):
        raise ProviderError("Stripe signature is invalid") from None
    if abs((int(time.time()) if now is None else now) - timestamp) > tolerance_seconds:
        raise ProviderError("Stripe signature is stale")
    expected = hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise ProviderError("Stripe signature is invalid")
    try:
        event = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderError("Stripe payload is invalid") from None
    if not isinstance(event, dict):
        raise ProviderError("Stripe payload is invalid")
    return event


_ANALYTICS_EVENTS = {
    "research_opened",
    "risk_radar_viewed",
    "thesis_saved",
    "alert_opened",
    "checkout_started",
}
_ANALYTICS_PROPERTIES = {"surface", "state", "source", "outcome"}


class SafeAnalytics:
    """Server-side, allowlisted PostHog events with no financial payload surface."""

    def __init__(
        self,
        api_key: str,
        *,
        host: str = "https://us.i.posthog.com",
        client: httpx.Client | None = None,
    ) -> None:
        self._key = api_key.strip()
        self._host = host.rstrip("/") + "/"
        self._client = client or httpx.Client(timeout=5, follow_redirects=False)

    def capture(self, distinct_id: str, event: str, properties: dict[str, str]) -> bool:
        if event not in _ANALYTICS_EVENTS or set(properties) - _ANALYTICS_PROPERTIES:
            raise ValueError("Analytics event or properties are not allowlisted")
        if any(not isinstance(value, str) or len(value) > 64 for value in properties.values()):
            raise ValueError("Analytics values must be short non-financial labels")
        anonymous = hashlib.sha256(distinct_id.encode()).hexdigest()
        try:
            response = self._client.post(
                urljoin(self._host, "capture/"),
                json={
                    "api_key": self._key,
                    "event": event,
                    "properties": {"distinct_id": anonymous, **properties},
                },
            )
            return response.is_success
        except httpx.HTTPError:
            return False
