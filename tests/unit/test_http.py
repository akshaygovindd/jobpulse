from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from jobpulse.http import FetchError, HttpClient, ReasonCode, parse_retry_after

MakeHttp = Callable[..., tuple[HttpClient, list[float]]]
URL = "https://example.test/x"


def sequence(*responses: httpx.Response | Exception) -> Callable[[httpx.Request], httpx.Response]:
    calls = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def test_success(make_http: MakeHttp) -> None:
    http, sleeps = make_http(sequence(httpx.Response(200, json={"ok": True})))
    assert http.get_json(URL) == {"ok": True}
    assert sleeps == []


def test_retries_5xx_then_succeeds(make_http: MakeHttp) -> None:
    http, sleeps = make_http(
        sequence(httpx.Response(503), httpx.Response(502), httpx.Response(200, json=[1]))
    )
    assert http.get_json(URL) == [1]
    assert len(sleeps) == 2


def test_429_honours_retry_after(make_http: MakeHttp) -> None:
    http, sleeps = make_http(
        sequence(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={}))
    )
    http.get_json(URL)
    assert sleeps == [7.0]


def test_retry_after_is_capped(make_http: MakeHttp) -> None:
    http, sleeps = make_http(
        sequence(httpx.Response(429, headers={"Retry-After": "9999"}), httpx.Response(200, json={}))
    )
    http.get_json(URL)
    assert sleeps == [120.0]


def test_rate_limited_exhausted(make_http: MakeHttp) -> None:
    http, _ = make_http(sequence(*[httpx.Response(429) for _ in range(3)]))
    with pytest.raises(FetchError) as e:
        http.get_json(URL)
    assert e.value.reason is ReasonCode.RATE_LIMITED


def test_404_is_board_not_found_without_retry(make_http: MakeHttp) -> None:
    http, sleeps = make_http(sequence(httpx.Response(404)))
    with pytest.raises(FetchError) as e:
        http.get_json(URL)
    assert e.value.reason is ReasonCode.BOARD_NOT_FOUND
    assert sleeps == []


def test_403_is_http_4xx(make_http: MakeHttp) -> None:
    http, _ = make_http(sequence(httpx.Response(403)))
    with pytest.raises(FetchError) as e:
        http.get_json(URL)
    assert e.value.reason is ReasonCode.HTTP_4XX


def test_timeout_retried_then_reported(make_http: MakeHttp) -> None:
    http, sleeps = make_http(sequence(*[httpx.ReadTimeout("slow") for _ in range(3)]))
    with pytest.raises(FetchError) as e:
        http.get_json(URL)
    assert e.value.reason is ReasonCode.TIMEOUT
    assert len(sleeps) == 2


def test_invalid_json_is_parse_error(make_http: MakeHttp) -> None:
    http, _ = make_http(sequence(httpx.Response(200, content=b"<html>")))
    with pytest.raises(FetchError) as e:
        http.get_json(URL)
    assert e.value.reason is ReasonCode.PARSE_ERROR


def test_parse_retry_after_forms() -> None:
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480.0) == 0.0
