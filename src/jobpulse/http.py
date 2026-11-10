"""Shared HTTP client: retries with exponential backoff + jitter, honours 429 Retry-After,
enforces timeouts, and maps every failure to a reason code."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base, wait_exponential_jitter

from jobpulse.config import HttpConfig
from jobpulse.logging import get_logger


class ReasonCode(StrEnum):
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    TRUNCATED = "truncated"
    PARSE_ERROR = "parse_error"
    BOARD_NOT_FOUND = "board_not_found"
    STORAGE_ERROR = "storage_error"
    UNEXPECTED_ERROR = "unexpected_error"


class FetchError(Exception):
    """A fetch that could not produce a complete result. Never implies closure."""

    def __init__(self, reason: ReasonCode, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class _RetryableError(Exception):
    def __init__(self, reason: ReasonCode, detail: str, retry_after_s: float | None = None):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.retry_after_s = retry_after_s


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Retry-After is either delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, when - (now if now is not None else time.time()))


class _WaitRetryAfterOrBackoff(wait_base):
    def __init__(self, cfg: HttpConfig) -> None:
        self._cap = cfg.retry_after_cap_s
        self._backoff = wait_exponential_jitter(
            multiplier=cfg.backoff_initial_s, max=cfg.backoff_max_s, jitter=cfg.backoff_initial_s
        )

    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, _RetryableError) and exc.retry_after_s is not None:
            return min(exc.retry_after_s, self._cap)
        return float(self._backoff(retry_state))


class HttpClient:
    def __init__(
        self,
        cfg: HttpConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg = cfg
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=cfg.timeout_s,
            headers={"User-Agent": cfg.user_agent, "Accept": "application/json"},
            transport=transport,
            follow_redirects=True,
        )
        self._log = get_logger(component="http")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _once(self, url: str, params: Mapping[str, str | int] | None) -> Any:
        try:
            resp = self._client.get(url, params=params)
        except httpx.TimeoutException as e:
            raise _RetryableError(ReasonCode.TIMEOUT, f"timeout: {e}") from e
        except httpx.TransportError as e:
            raise _RetryableError(ReasonCode.NETWORK_ERROR, f"transport: {e}") from e

        status = resp.status_code
        if status == 429:
            raise _RetryableError(
                ReasonCode.RATE_LIMITED,
                "HTTP 429",
                parse_retry_after(resp.headers.get("Retry-After")),
            )
        if status >= 500:
            raise _RetryableError(ReasonCode.HTTP_5XX, f"HTTP {status}")
        if status == 404:
            raise FetchError(ReasonCode.BOARD_NOT_FOUND, f"HTTP 404 for {url}")
        if status >= 400:
            raise FetchError(ReasonCode.HTTP_4XX, f"HTTP {status} for {url}")
        try:
            return resp.json()
        except ValueError as e:
            raise FetchError(ReasonCode.PARSE_ERROR, f"invalid JSON from {url}: {e}") from e

    def get_json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        def _log_retry(state: RetryCallState) -> None:
            exc = state.outcome.exception() if state.outcome else None
            self._log.warning(
                "http_retry",
                url=url,
                attempt=state.attempt_number,
                reason=getattr(exc, "reason", None),
                detail=str(exc),
            )

        retrying = Retrying(
            stop=stop_after_attempt(self._cfg.max_attempts),
            wait=_WaitRetryAfterOrBackoff(self._cfg),
            retry=retry_if_exception(lambda e: isinstance(e, _RetryableError)),
            sleep=self._sleep,
            before_sleep=_log_retry,
            reraise=True,
        )
        try:
            return retrying(self._once, url, params)
        except _RetryableError as e:
            raise FetchError(e.reason, f"{e.detail} after {self._cfg.max_attempts} attempts") from e
