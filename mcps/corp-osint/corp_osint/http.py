"""Async HTTP client with per-host throttling, tenacity retries, and proxy support."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import aiohttp
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
)

from .config import HttpConfig, ThrottleConfig
from .log import get_logger
from .util import host_matches_no_proxy

log = get_logger("http")


class RateLimitedError(Exception):
    """Raised on HTTP 429 so tenacity can retry."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        already_slept: bool = False,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.already_slept = already_slept


class TransientHttpError(Exception):
    """Raised on 5xx / network blips for retry."""

    def __init__(self, message: str, *, already_slept: bool = False) -> None:
        super().__init__(message)
        self.already_slept = already_slept


def parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After header (seconds or HTTP-date) → sleep seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


# Rate-limit sleeps: +200ms each chunk, never more than 3s total.
RATE_LIMIT_STEP_SEC = 0.2
RATE_LIMIT_MAX_SEC = 3.0


def backoff_seconds(
    attempt: int,
    *,
    min_wait: float = RATE_LIMIT_STEP_SEC,
    max_wait: float = RATE_LIMIT_MAX_SEC,
) -> float:
    """Backoff for attempt number (1-based): attempt×200ms, capped at ``max_wait``."""
    n = max(1, int(attempt))
    return min(max_wait, max(min_wait, RATE_LIMIT_STEP_SEC * n))


async def incremental_sleep(
    total: float,
    *,
    reason: str = "backoff",
    logger: logging.Logger | None = None,
    step: float = RATE_LIMIT_STEP_SEC,
    max_total: float = RATE_LIMIT_MAX_SEC,
) -> None:
    """Sleep up to ``max_total`` seconds in +``step`` increments (default 200ms / 3s).

    Chunk lengths grow linearly: 200ms, 400ms, 600ms, … until the capped total
    is exhausted. Logs each chunk for progress visibility.
    """
    remaining = min(max(0.0, float(total)), max(0.0, float(max_total)))
    if remaining <= 0:
        return
    sink = logger or log
    increment = max(0.05, float(step))
    chunk = increment
    n = 0
    while remaining > 1e-9:
        n += 1
        this = min(chunk, remaining)
        sink.info(
            "rate-limit %s chunk=%d sleep=%.2fs remaining=%.2fs",
            reason,
            n,
            this,
            remaining,
        )
        await asyncio.sleep(this)
        remaining -= this
        chunk = min(max_total, chunk + increment)


def _tcp() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(ttl_dns_cache=300, enable_cleanup_closed=True)


def _build_proxy_transport(proxy: str) -> tuple[aiohttp.BaseConnector, str | None, bool]:
    """
    Returns ``(connector, per_request_proxy, via_connector)``.

    Prefer ``aiohttp_socks.ProxyConnector`` for http/https/socks. Without that
    package, HTTP(S) proxies fall back to aiohttp's per-request ``proxy=``.
    """
    raw = proxy if "://" in proxy else f"http://{proxy}"
    scheme = urlparse(raw).scheme.lower()
    try:
        from aiohttp_socks import ProxyConnector

        return ProxyConnector.from_url(raw, rdns=True), None, True
    except ImportError:
        if scheme.startswith("socks"):
            raise RuntimeError(
                "SOCKS proxy requires aiohttp-socks — pip install aiohttp-socks"
            ) from None
        return _tcp(), raw, False


class AsyncHttpClient:
    """Shared aiohttp session with host-aware concurrency + spacing + optional proxy."""

    def __init__(
        self,
        http: HttpConfig | None = None,
        throttle: ThrottleConfig | None = None,
    ) -> None:
        self.http = http or HttpConfig()
        self.throttle = throttle or ThrottleConfig()
        self._session: aiohttp.ClientSession | None = None
        self._direct: aiohttp.ClientSession | None = None  # NO_PROXY bypass
        self._proxy_via_connector = False
        self._request_proxy: str | None = None  # per-request HTTP proxy fallback
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_ok: dict[str, float] = {}
        self._cooldowns: dict[str, float] = {}  # host key → monotonic deadline
        self._global_sem = asyncio.Semaphore(self.throttle.max_concurrent)

    async def __aenter__(self) -> AsyncHttpClient:
        timeout = aiohttp.ClientTimeout(total=self.http.timeout_sec)
        headers = {"User-Agent": self.http.user_agent, "Accept": "*/*"}
        proxy = self.http.proxy

        if proxy:
            connector, req_proxy, via = _build_proxy_transport(proxy)
            self._proxy_via_connector = via
            self._request_proxy = req_proxy
            log.info(
                "HTTP proxy enabled (%s) %s",
                "connector" if via else "per-request",
                _redact_proxy(proxy),
            )
        else:
            connector = _tcp()
            log.debug("HTTP proxy disabled")

        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            connector=connector,
        )
        if proxy and self.http.no_proxy:
            self._direct = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
                connector=_tcp(),
            )
            log.debug("NO_PROXY bypass hosts=%s", list(self.http.no_proxy)[:12])
        return self

    async def __aexit__(self, *args: Any) -> None:
        for sess in (self._session, self._direct):
            if sess and not sess.closed:
                await sess.close()
        self._session = None
        self._direct = None

    def _host_key(self, url: str) -> str:
        host = urlparse(url).hostname or "default"
        for needle in self.throttle.host_limits:
            if needle in host:
                return needle
        return host

    def _limits(self, key: str) -> tuple[int, float]:
        return self.throttle.host_limits.get(
            key, (self.throttle.max_concurrent, self.throttle.min_interval_sec)
        )

    def _sem(self, key: str) -> asyncio.Semaphore:
        if key not in self._semaphores:
            conc, _ = self._limits(key)
            self._semaphores[key] = asyncio.Semaphore(max(1, conc))
        return self._semaphores[key]

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _pick_session(self, url: str) -> tuple[aiohttp.ClientSession, str | None]:
        """Return (session, per-request proxy|None)."""
        assert self._session is not None
        host = urlparse(url).hostname
        bypass = host_matches_no_proxy(host, self.http.no_proxy)
        if bypass and self._direct is not None:
            return self._direct, None
        if bypass or self._proxy_via_connector:
            return self._session, None
        return self._session, self._request_proxy

    def mark_cooldown(self, url_or_host: str, seconds: float) -> None:
        """Force a host to wait before the next request (e.g. after 429)."""
        key = self._host_key(url_or_host) if "://" in url_or_host else url_or_host
        deadline = time.monotonic() + max(0.0, seconds)
        self._cooldowns[key] = max(self._cooldowns.get(key, 0.0), deadline)
        self._next_ok[key] = max(self._next_ok.get(key, 0.0), deadline)

    async def _pace(self, key: str) -> None:
        _, interval = self._limits(key)
        async with self._lock(key):
            now = time.monotonic()
            target = max(self._next_ok.get(key, 0.0), self._cooldowns.get(key, 0.0))
            wait_for = target - now
            if wait_for > 0.05:
                await incremental_sleep(
                    wait_for,
                    reason=f"pace:{key}",
                    logger=log,
                )
            elif wait_for > 0:
                await asyncio.sleep(wait_for)
            self._next_ok[key] = time.monotonic() + interval

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        json_body: Any = None,
        timeout: float | None = None,
        attempts: int | None = None,
        retry_on_client_error: bool = True,
    ) -> tuple[int, bytes, dict[str, str]]:
        if not self._session:
            raise RuntimeError("AsyncHttpClient not started; use async with")

        key = self._host_key(url)
        attempt_box = {"n": 0}
        http_cfg = self.http
        max_attempts = max(1, attempts if attempts is not None else self.http.retries)
        retry_types: tuple[type[BaseException], ...] = (RateLimitedError, TransientHttpError)
        if retry_on_client_error:
            retry_types = (*retry_types, aiohttp.ClientError)

        def _wait(retry_state: RetryCallState) -> float:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            # Incremental sleep already performed in _do for rate/transient errors.
            if isinstance(exc, (RateLimitedError, TransientHttpError)) and getattr(
                exc, "already_slept", False
            ):
                return 0.0
            if isinstance(exc, RateLimitedError) and exc.retry_after is not None:
                return float(exc.retry_after)
            n = max(1, retry_state.attempt_number)
            return backoff_seconds(
                n, min_wait=http_cfg.retry_min_wait, max_wait=http_cfg.retry_max_wait
            )

        @retry(
            reraise=True,
            stop=stop_after_attempt(max_attempts),
            wait=_wait,
            retry=retry_if_exception_type(retry_types),
        )
        async def _do() -> tuple[int, bytes, dict[str, str]]:
            attempt_box["n"] += 1
            async with self._global_sem, self._sem(key):
                await self._pace(key)
                session, req_proxy = self._pick_session(url)
                kw: dict[str, Any] = {"headers": headers or {}}
                if json_body is not None:
                    kw["json"] = json_body
                elif data is not None:
                    kw["data"] = data
                if timeout is not None:
                    kw["timeout"] = aiohttp.ClientTimeout(total=timeout)
                if req_proxy:
                    kw["proxy"] = req_proxy
                try:
                    async with session.request(method, url, **kw) as resp:
                        body = await resp.read()
                        hdrs = {k.lower(): v for k, v in resp.headers.items()}
                        if resp.status == 429:
                            ra = parse_retry_after(hdrs.get("retry-after"))
                            sleep_for = (
                                ra
                                if ra is not None
                                else backoff_seconds(
                                    attempt_box["n"],
                                    min_wait=self.http.retry_min_wait,
                                    max_wait=self.http.retry_max_wait,
                                )
                            )
                            sleep_for = min(
                                RATE_LIMIT_MAX_SEC,
                                max(sleep_for, self.http.retry_min_wait),
                            )
                            self.mark_cooldown(url, sleep_for)
                            log.warning(
                                "HTTP 429 %s attempt=%d retry_after=%.1fs",
                                url,
                                attempt_box["n"],
                                sleep_for,
                            )
                            if attempt_box["n"] < max_attempts:  # skip sleep on last try
                                await incremental_sleep(
                                    sleep_for,
                                    reason=f"429:{key}",
                                    logger=log,
                                )
                            raise RateLimitedError(
                                f"429 {url} (retry_after={sleep_for:.1f}s)",
                                retry_after=sleep_for,
                                already_slept=True,
                            )
                        if resp.status >= 500 and max_attempts > 1:
                            sleep_for = backoff_seconds(
                                attempt_box["n"],
                                min_wait=self.http.retry_min_wait,
                                max_wait=self.http.retry_max_wait,
                            )
                            log.debug(
                                "HTTP %s %s attempt=%d sleep=%.1fs",
                                resp.status,
                                url,
                                attempt_box["n"],
                                sleep_for,
                            )
                            if attempt_box["n"] < max_attempts:  # skip sleep on last try
                                await incremental_sleep(
                                    sleep_for,
                                    reason=f"{resp.status}:{key}",
                                    logger=log,
                                )
                            raise TransientHttpError(
                                f"{resp.status} {url}",
                                already_slept=True,
                            )
                        log.debug("%s %s → %s", method, url, resp.status)
                        return resp.status, body, hdrs
                except aiohttp.ClientError as e:
                    log.debug("client error %s %s: %s", method, url, e)
                    if retry_on_client_error and max_attempts > 1:
                        sleep_for = backoff_seconds(
                            attempt_box["n"],
                            min_wait=self.http.retry_min_wait,
                            max_wait=self.http.retry_max_wait,
                        )
                        if attempt_box["n"] < max_attempts:  # skip sleep on last try
                            await incremental_sleep(
                                sleep_for,
                                reason=f"client:{key}",
                                logger=log,
                            )
                        raise TransientHttpError(str(e), already_slept=True) from e
                    raise

        return await _do()

    async def get(self, url: str, **kwargs: Any) -> tuple[int, bytes, dict[str, str]]:
        return await self.request("GET", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        status, body, _ = await self.get(
            url,
            headers={"Accept": "application/json", **(kwargs.pop("headers", {}) or {})},
            **kwargs,
        )
        if status >= 400:
            raise TransientHttpError(f"HTTP {status} for {url}: {body[:200]!r}")
        return json.loads(body.decode("utf-8", errors="replace"))

    async def get_text(self, url: str, **kwargs: Any) -> tuple[int, str]:
        status, body, _ = await self.get(url, **kwargs)
        return status, body.decode("utf-8", errors="replace")

    async def probe(
        self, url: str, *, timeout: float = 8.0
    ) -> tuple[int | None, str, str | None]:
        """One-shot fetch for dangling/parking checks — no client-error retries."""
        try:
            status, body, _ = await self.request(
                "GET",
                url,
                timeout=timeout,
                attempts=1,
                retry_on_client_error=False,
            )
            return status, body.decode("utf-8", errors="replace"), None
        except Exception as e:  # noqa: BLE001
            return None, "", str(e)


def _redact_proxy(url: str) -> str:
    """Log-safe proxy URL (strip credentials)."""
    try:
        p = urlparse(url if "://" in url else f"http://{url}")
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{host}{port}"
    except Exception:  # noqa: BLE001
        return "<proxy>"
