"""EDGAR client: UA enforcement, retry/backoff, throttle, cache, CIK normalization."""
from __future__ import annotations

import httpx
import pytest

from finwatch.ingest.edgar import (
    EdgarClient,
    EdgarHTTPError,
    RateLimiter,
    RetryableHTTPError,
    normalize_cik,
)


def _client(handler, **kw) -> EdgarClient:
    return EdgarClient(
        "Test User t@example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
        **kw,
    )


def test_normalize_cik():
    assert normalize_cik(320193) == "0000320193"
    assert normalize_cik("CIK0000320193") == "0000320193"
    assert normalize_cik("320193") == "0000320193"
    with pytest.raises(ValueError):
        normalize_cik("not-a-cik")


def test_user_agent_required():
    with pytest.raises(ValueError):
        EdgarClient("")
    with pytest.raises(ValueError):
        EdgarClient("   ")


def test_user_agent_forced_even_on_injected_client():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, json={})

    _client(handler).get_json("https://data.sec.gov/x.json")
    assert seen["ua"] == "Test User t@example.com"


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] == 1 else httpx.Response(200, json={"ok": 1})

    slept = []
    ec = EdgarClient(
        "UA u@x.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=slept.append,
    )
    assert ec.get_json("https://data.sec.gov/x.json") == {"ok": 1}
    assert calls["n"] == 2 and len(slept) >= 1


def test_retries_on_403_and_5xx():
    for status in (403, 500, 503):
        calls = {"n": 0}

        def handler(req, _status=status, _calls=calls):
            _calls["n"] += 1
            return httpx.Response(_status) if _calls["n"] == 1 else httpx.Response(200, json={})

        _client(handler).get_json("https://data.sec.gov/x.json")
        assert calls["n"] == 2, f"status {status} should have retried"


def test_exhausts_retries_and_reraises():
    def handler(req):
        return httpx.Response(503)

    with pytest.raises(RetryableHTTPError):
        _client(handler, max_attempts=3).get_json("https://data.sec.gov/x.json")


def test_permanent_404_is_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(EdgarHTTPError):
        _client(handler).get_json("https://data.sec.gov/missing.json")
    assert calls["n"] == 1  # no retry on a permanent error


def test_rate_limiter_enforces_min_interval():
    t = {"v": 0.0}
    rl = RateLimiter(8, clock=lambda: t["v"], sleep=lambda s: t.__setitem__("v", t["v"] + s))
    rl.wait()
    before = t["v"]
    rl.wait()
    assert round(t["v"] - before, 6) == 0.125  # 1/8 s


def test_cache_serves_second_call(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"v": calls["n"]})

    ec = EdgarClient(
        "UA u@x.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_dir=tmp_path,
    )
    first = ec.company_tickers()
    second = ec.company_tickers()
    assert calls["n"] == 1 and first == second  # second read hit the cache
