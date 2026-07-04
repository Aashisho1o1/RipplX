"""EDGAR HTTP client — SEC-etiquette-compliant.

Hard requirements baked in (CLAUDE.md §4): identify via a non-empty User-Agent
(refuse to construct without one), throttle to ≤ 8 requests/second, exponential
backoff on 429/403/5xx, and cache aggressively (filings are immutable — fetch once,
store forever). The httpx client, clock, and sleep are all injectable so tests run
with zero network and zero real waiting.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

DEFAULT_MAX_REQUESTS_PER_SEC = 8  # SEC policy ceiling
DEFAULT_MAX_ATTEMPTS = 5


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

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            delta = now - self._last
            if delta < self.min_interval:
                self._sleep(self.min_interval - delta)
        self._last = self._clock()


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
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValueError("SEC requires a non-empty User-Agent")
        self.user_agent = user_agent
        self._sleep = sleep
        self.max_attempts = max_attempts
        if client is None:
            client = httpx.Client(
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=timeout,
                follow_redirects=True,  # SEC endpoints may redirect (http→https, trailing /)
            )
        # SEC compliance: our identifying User-Agent must always win, even on an
        # injected client (httpx sets its own default UA that setdefault won't replace).
        client.headers["User-Agent"] = user_agent
        self._client = client
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.rate_limiter = rate_limiter or RateLimiter(clock=clock, sleep=sleep)

    # -- low level ---------------------------------------------------------
    def _cache_path(self, name: str | None) -> Path | None:
        return self.cache_dir / name if (self.cache_dir and name) else None

    def _fetch(self, url: str) -> bytes:
        for attempt in Retrying(
            sleep=self._sleep,
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=0.5, max=8.0),
            retry=retry_if_exception_type((RetryableHTTPError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                self.rate_limiter.wait()
                resp = self._client.get(url)
                sc = resp.status_code
                if sc == 429 or sc == 403 or sc >= 500:
                    raise RetryableHTTPError(sc, url)
                if sc >= 300:
                    # 3xx (an unfollowed redirect) or 4xx: never return the body as data.
                    raise EdgarHTTPError(sc, url)
                return resp.content
        raise AssertionError("unreachable")  # pragma: no cover

    def get_bytes(
        self, url: str, *, cache_name: str | None = None, force_refresh: bool = False
    ) -> bytes:
        cpath = self._cache_path(cache_name)
        if cpath is not None and cpath.exists() and not force_refresh:
            return cpath.read_bytes()
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
        return self.get_bytes(url, cache_name=cache_name, force_refresh=False)

    def close(self) -> None:
        self._client.close()
