"""
EDGAR HTTP client — SEC-etiquette-compliant.

Hard requirements baked in (CLAUDE.md §4): identify via a non-empty User-Agent
(refuse to construct without one), throttle to ≤ 8 requests/second, exponential
backoff on 429/403/5xx, and cache aggressively (filings are immutable — fetch once,
store forever). The httpx client, clock, and sleep are all injectable so tests run
with zero network and zero real waiting.
"""

#AS: Last comments talk about hard requirement: make sure it'ss ensible and logicala nd ibndustry dstandard ahrd req. We are in prorotuype, so good to stay elana nd fleixble.

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

DEFAULT_MAX_REQUESTS_PER_SEC = 8  # SEC policy ceiling
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024

_ALLOWED_SEC_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})
_SAFE_CACHE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_SUBMISSIONS_PAGE_NAME = re.compile(r"CIK\d{10}-submissions-\d{3}\.json\Z")


def normalize_cik(cik: int | str) -> str:
    """Return the 10-digit zero-padded CIK EDGAR uses in URLs and keys."""
    s = str(cik).strip()
    if s.upper().startswith("CIK"):
        s = s[3:]
    if not s.isdigit():
        raise ValueError(f"invalid CIK: {cik!r}")
    return s.zfill(10)


class RetryableHTTPError(Exception):
    """Transient HTTP failure (429/403/5xx) — safe to retry with backoff."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"retryable HTTP {status_code} for {url}")
        self.status_code = status_code
        self.url = url


class EdgarHTTPError(Exception):
    """Permanent HTTP failure (e.g. 404) — do not retry."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} for {url}")
        self.status_code = status_code
        self.url = url


class EdgarResponseTooLarge(Exception):
    """An SEC response exceeded the bounded launch download budget."""

    def __init__(self, limit: int, url: str) -> None:
        super().__init__(f"SEC response exceeded the {limit}-byte limit for {url}")
        self.limit = limit
        self.url = url


class RateLimiter:
    """Minimum-interval throttle. Deterministic via injected clock + sleep."""

    def __init__(
        self,
        max_per_sec: float = DEFAULT_MAX_REQUESTS_PER_SEC,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_sec <= 0:
            raise ValueError("max_per_sec must be positive")
        self.min_interval = 1.0 / max_per_sec
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._lock = Lock()

    def wait(self) -> None:
        # The production limiter is shared by every EDGAR client in this process.
        # Hold the lock through the wait so concurrent clients cannot all observe
        # the same stale timestamp and burst past the SEC request ceiling.
        with self._lock:
            now = self._clock()
            if self._last is not None:
                delta = now - self._last
                if delta < self.min_interval:
                    self._sleep(self.min_interval - delta)
            self._last = self._clock()


_PROCESS_RATE_LIMITER = RateLimiter()


class EdgarClient:
    """Fetch SEC JSON/artifacts with UA enforcement, throttle, backoff, and cache."""

    def __init__(
        self,
        user_agent: str,
        *,
        client: httpx.Client | None = None,
        cache_dir: str | Path | None = None,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout: float = 30.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValueError("SEC requires a non-empty User-Agent")
        self.user_agent = user_agent
        self._sleep = sleep
        self.max_attempts = max_attempts
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.max_response_bytes = max_response_bytes
        injected_client = client is not None
        if client is None:
            client = httpx.Client(
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=timeout,
                # Known EDGAR endpoints are already canonical HTTPS URLs. Redirects are
                # rejected so an upstream response can never pivot this client off SEC.
                follow_redirects=False,
            )
        # SEC compliance: our identifying User-Agent must always win, even on an
        # injected client (httpx sets its own default UA that setdefault won't replace).
        client.headers["User-Agent"] = user_agent
        self._client = client
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.rate_limiter = rate_limiter or (
            RateLimiter(clock=clock, sleep=sleep) if injected_client else _PROCESS_RATE_LIMITER
        )

    # -- low level ---------------------------------------------------------
    def _cache_path(self, name: str | None) -> Path | None:
        if not self.cache_dir or not name:
            return None

        relative = Path(name)
        if relative.is_absolute() or not relative.parts:
            raise ValueError("cache name must be a relative path")
        if any(not _SAFE_CACHE_COMPONENT.fullmatch(part) for part in relative.parts):
            raise ValueError("cache name contains an unsafe path component")

        root = self.cache_dir.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            # resolve() also catches an existing child symlink that points outside root.
            raise ValueError("cache path escapes the configured cache directory") from exc
        return candidate

    @staticmethod
    def _validate_sec_url(url: str) -> None:
        """Reject every outbound destination except canonical HTTPS SEC hosts."""
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid SEC URL") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_SEC_HOSTS
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("URL must use HTTPS on an approved SEC host")

    def _fetch(self, url: str) -> bytes:
        self._validate_sec_url(url)
        for attempt in Retrying(
            sleep=self._sleep,
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=0.5, max=8.0),
            retry=retry_if_exception_type((RetryableHTTPError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                self.rate_limiter.wait()
                # Override an injected client's defaults as well as our own. A redirect
                # is an error, not an instruction to make a second outbound request.
                with self._client.stream("GET", url, follow_redirects=False) as resp:
                    sc = resp.status_code
                    if sc == 429 or sc == 403 or sc >= 500:
                        raise RetryableHTTPError(sc, url)
                    if sc >= 300:
                        # 3xx (an unfollowed redirect) or 4xx: never return the body as data.
                        raise EdgarHTTPError(sc, url)
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self.max_response_bytes:
                        raise EdgarResponseTooLarge(self.max_response_bytes, url)
                    chunks: list[bytes] = []
                    received = 0
                    for chunk in resp.iter_bytes():
                        received += len(chunk)
                        if received > self.max_response_bytes:
                            raise EdgarResponseTooLarge(self.max_response_bytes, url)
                        chunks.append(chunk)
                    return b"".join(chunks)
        raise AssertionError("unreachable")  # pragma: no cover

    def get_bytes(
        self, url: str, *, cache_name: str | None = None, force_refresh: bool = False
    ) -> bytes:
        # Validate before a cache read: callers cannot use a primed cache entry to make
        # an otherwise-forbidden destination appear accepted.
        self._validate_sec_url(url)
        cpath = self._cache_path(cache_name)
        if cpath is not None and cpath.exists() and not force_refresh:
            if cpath.stat().st_size > self.max_response_bytes:
                raise EdgarResponseTooLarge(self.max_response_bytes, url)
            cached = cpath.read_bytes()
            if len(cached) > self.max_response_bytes:  # file could grow after stat()
                raise EdgarResponseTooLarge(self.max_response_bytes, url)
            return cached
        content = self._fetch(url)
        if cpath is not None:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: a crash/ENOSPC mid-write must never leave a partial file
            # at the canonical path (it would be served forever by the cache-first read).
            tmp = cpath.with_name(cpath.name + ".tmp")
            tmp.write_bytes(content)
            os.replace(tmp, cpath)
        return content

    def get_json(
        self, url: str, *, cache_name: str | None = None, force_refresh: bool = False
    ) -> dict:
        raw = self.get_bytes(url, cache_name=cache_name, force_refresh=force_refresh)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # A pre-existing poisoned cache file can't parse — force one fresh fetch.
            if cache_name and not force_refresh:
                raw = self.get_bytes(url, cache_name=cache_name, force_refresh=True)
                return json.loads(raw)
            raise

    # -- SEC endpoints -----------------------------------------------------
    def company_tickers(self, *, force_refresh: bool = False) -> dict:
        """ticker→CIK master file. Changes rarely; cache-first."""
        return self.get_json(
            SEC_TICKERS_URL, cache_name="company_tickers.json", force_refresh=force_refresh
        )

    def submissions(self, cik: int | str, *, force_refresh: bool = True) -> dict:
        """Filing index + issuer profile. Updates over time; refresh by default."""
        c = normalize_cik(cik)
        return self.get_json(
            SUBMISSIONS_URL.format(cik=c),
            cache_name=f"submissions_CIK{c}.json",
            force_refresh=force_refresh,
        )

    def submissions_page(self, name: str) -> dict:
        """An older paginated submissions page (``subs['filings']['files'][i]['name']``).

        SEC caps ``filings.recent`` at ~1000 rows; filings older than that roll off into
        these pages, which are effectively immutable once written — so cache forever.
        """
        if not _SUBMISSIONS_PAGE_NAME.fullmatch(name):
            raise ValueError("invalid SEC submissions page name")
        return self.get_json(
            SUBMISSIONS_PAGE_URL.format(name=name),
            cache_name=f"submissions_page_{name}",
            force_refresh=False,
        )

    def companyfacts(self, cik: int | str, *, force_refresh: bool = True) -> dict:
        """Full XBRL fact history for a CIK. Updates with each filing; refresh."""
        c = normalize_cik(cik)
        return self.get_json(
            COMPANYFACTS_URL.format(cik=c),
            cache_name=f"companyfacts_CIK{c}.json",
            force_refresh=force_refresh,
        )

    def fetch_primary_doc(self, url: str, *, cache_name: str | None = None) -> bytes:
        """Fetch an immutable filing artifact; always safe to cache forever."""
        if cache_name is None:
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            cache_name = f"filings/{digest}.bin"
        return self.get_bytes(url, cache_name=cache_name, force_refresh=False)

    def close(self) -> None:
        self._client.close()
