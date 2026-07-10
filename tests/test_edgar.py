"""EDGAR client: UA enforcement, retry/backoff, throttle, cache, CIK normalization."""
from __future__ import annotations

import hashlib

import httpx
import pytest

from finwatch.ingest.edgar import (
    EdgarClient,
    EdgarHTTPError,
    EdgarResponseTooLarge,
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


def test_response_size_is_bounded_before_cache_write(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, content=b"123456789")

    client = _client(handler, cache_dir=tmp_path, max_response_bytes=8)
    with pytest.raises(EdgarResponseTooLarge, match="8-byte limit"):
        client.get_bytes(
            "https://www.sec.gov/Archives/doc.htm",
            cache_name="filings/doc.htm",
        )

    assert calls["n"] == 1
    assert not (tmp_path / "filings" / "doc.htm").exists()


def test_oversized_existing_cache_is_never_loaded(tmp_path):
    (tmp_path / "large.bin").write_bytes(b"123456789")
    client = _client(
        lambda req: pytest.fail(f"unexpected network request: {req.url}"),
        cache_dir=tmp_path,
        max_response_bytes=8,
    )

    with pytest.raises(EdgarResponseTooLarge, match="8-byte limit"):
        client.get_bytes(
            "https://www.sec.gov/Archives/doc.htm",
            cache_name="large.bin",
        )


def test_rate_limiter_enforces_min_interval():
    t = {"v": 0.0}
    rl = RateLimiter(8, clock=lambda: t["v"], sleep=lambda s: t.__setitem__("v", t["v"] + s))
    rl.wait()
    before = t["v"]
    rl.wait()
    assert round(t["v"] - before, 6) == 0.125  # 1/8 s


def test_production_clients_share_one_process_rate_limiter():
    first = EdgarClient("UA first@example.com")
    second = EdgarClient("UA second@example.com")
    try:
        assert first.rate_limiter is second.rate_limiter
    finally:
        first.close()
        second.close()


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


def test_primary_document_uses_safe_default_immutable_cache_key(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, content=b"filing")

    client = _client(handler, cache_dir=tmp_path)
    url = "https://www.sec.gov/Archives/edgar/data/320193/aapl.htm"
    assert client.fetch_primary_doc(url) == b"filing"
    assert client.fetch_primary_doc(url) == b"filing"

    expected = tmp_path / "filings" / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"
    assert expected.read_bytes() == b"filing"
    assert calls["n"] == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/Archives/doc.htm",
        "https://example.com/Archives/doc.htm",
        "https://www.sec.gov.evil.example/Archives/doc.htm",
        "https://www.sec.gov@127.0.0.1/latest/meta-data",
        "https://data.sec.gov:8443/submissions/CIK0000320193.json",
    ],
)
def test_rejects_non_sec_outbound_destinations_before_network(url):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, content=b"unexpected")

    with pytest.raises(ValueError, match="approved SEC host"):
        _client(handler).get_bytes(url)
    assert calls["n"] == 0


def test_redirect_is_not_followed_even_when_injected_client_follows_by_default():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.host == "data.sec.gov":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/metadata"})
        return httpx.Response(200, content=b"internal")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    ec = EdgarClient("UA u@x.com", client=client, sleep=lambda _s: None)
    with pytest.raises(EdgarHTTPError) as exc_info:
        ec.get_bytes("https://data.sec.gov/submissions/CIK0000320193.json")
    assert exc_info.value.status_code == 302
    assert seen == ["https://data.sec.gov/submissions/CIK0000320193.json"]


def test_forbidden_url_cannot_be_hidden_behind_a_primed_cache_entry(tmp_path):
    (tmp_path / "primed.json").write_bytes(b"cached")
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, content=b"network")

    ec = _client(handler, cache_dir=tmp_path)
    with pytest.raises(ValueError, match="approved SEC host"):
        ec.get_bytes("http://127.0.0.1/metadata", cache_name="primed.json")
    assert calls["n"] == 0


@pytest.mark.parametrize(
    "name",
    [
        "../outside.json",
        "subdir/page.json",
        "CIK0000320193-submissions-001.json/../../outside.json",
        "https://example.com/page.json",
        "CIK0000320193-submissions-1.json",
    ],
)
def test_submissions_page_rejects_noncanonical_external_names(name, tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={})

    ec = _client(handler, cache_dir=tmp_path)
    with pytest.raises(ValueError, match="submissions page name"):
        ec.submissions_page(name)
    assert calls["n"] == 0
    assert not list(tmp_path.iterdir())


def test_submissions_page_accepts_canonical_sec_filename(tmp_path):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"ok": True})

    ec = _client(handler, cache_dir=tmp_path)
    name = "CIK0000320193-submissions-001.json"
    assert ec.submissions_page(name) == {"ok": True}
    assert seen == [f"https://data.sec.gov/submissions/{name}"]
    assert (tmp_path / f"submissions_page_{name}").is_file()


def test_cache_rejects_traversal_and_existing_symlink_escape(tmp_path):
    cache = tmp_path / "cache"
    outside = tmp_path / "outside"
    cache.mkdir()
    outside.mkdir()

    ec = _client(lambda req: httpx.Response(200, content=b"data"), cache_dir=cache)
    with pytest.raises(ValueError, match="unsafe path component"):
        ec.get_bytes("https://www.sec.gov/Archives/doc.htm", cache_name="../outside.json")

    (cache / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        ec.get_bytes("https://www.sec.gov/Archives/doc.htm", cache_name="linked/doc.htm")
    assert not (outside / "doc.htm").exists()


def test_cache_allows_confined_internal_namespace(tmp_path):
    ec = _client(lambda req: httpx.Response(200, content=b"filing"), cache_dir=tmp_path)
    content = ec.fetch_primary_doc(
        "https://www.sec.gov/Archives/edgar/data/320193/doc.htm",
        cache_name="filings/000032019324000001_doc.htm",
    )
    assert content == b"filing"
    assert (tmp_path / "filings" / "000032019324000001_doc.htm").read_bytes() == content
