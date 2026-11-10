from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from jobpulse.connectors.greenhouse import GreenhouseConnector
from jobpulse.connectors.lever import LeverConnector
from jobpulse.http import FetchError, HttpClient, ReasonCode
from tests.conftest import load_fixture

MakeHttp = Callable[..., tuple[HttpClient, list[float]]]


def json_handler(payload: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=payload)


# --- Greenhouse --------------------------------------------------------------------------


def test_greenhouse_complete(make_http: MakeHttp) -> None:
    fixture = load_fixture("greenhouse_jobs.json")
    http, _ = make_http(json_handler(fixture))
    pages = list(GreenhouseConnector(http).fetch_company("everquote"))
    assert len(pages) == 1
    assert pages[0].payload == fixture
    assert pages[0].external_ids == tuple(str(j["id"]) for j in fixture["jobs"])


def test_greenhouse_requests_content(make_http: MakeHttp) -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})

    http, _ = make_http(handler)
    pages = list(GreenhouseConnector(http).fetch_company("acme"))
    assert pages[0].external_ids == ()
    assert seen[0].path == "/v1/boards/acme/jobs"
    assert seen[0].params["content"] == "true"


def test_greenhouse_total_mismatch_is_truncated(make_http: MakeHttp) -> None:
    fixture = load_fixture("greenhouse_jobs.json")
    fixture["meta"]["total"] = 99
    http, _ = make_http(json_handler(fixture))
    with pytest.raises(FetchError) as e:
        list(GreenhouseConnector(http).fetch_company("everquote"))
    assert e.value.reason is ReasonCode.TRUNCATED


def test_greenhouse_bad_shape_is_parse_error(make_http: MakeHttp) -> None:
    http, _ = make_http(json_handler({"unexpected": 1}))
    with pytest.raises(FetchError) as e:
        list(GreenhouseConnector(http).fetch_company("x"))
    assert e.value.reason is ReasonCode.PARSE_ERROR


# --- Lever -------------------------------------------------------------------------------


def lever_pages(postings: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params["skip"])
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json=postings[skip : skip + limit])

    return handler


def test_lever_paginates_until_short_page(make_http: MakeHttp) -> None:
    postings = load_fixture("lever_postings.json")  # 3 postings
    http, _ = make_http(lever_pages(postings))
    pages = list(LeverConnector(http, page_size=2).fetch_company("wealthfront"))
    assert [p.number for p in pages] == [1, 2]
    assert sum(len(p.external_ids) for p in pages) == 3


def test_lever_exact_multiple_ends_on_empty_page(make_http: MakeHttp) -> None:
    postings = load_fixture("lever_postings.json")[:2]
    http, _ = make_http(lever_pages(postings))
    pages = list(LeverConnector(http, page_size=2).fetch_company("x"))
    assert [p.number for p in pages] == [1]  # empty trailing page is not stored


def test_lever_empty_board_yields_one_empty_page(make_http: MakeHttp) -> None:
    http, _ = make_http(lever_pages([]))
    pages = list(LeverConnector(http).fetch_company("x"))
    assert len(pages) == 1
    assert pages[0].external_ids == ()


def test_lever_pagination_drift_is_truncated(make_http: MakeHttp) -> None:
    p = load_fixture("lever_postings.json")
    drifted = [p[0], p[1], p[1], p[2]]  # a posting shifted into the next page
    http, _ = make_http(lever_pages(drifted))
    with pytest.raises(FetchError) as e:
        list(LeverConnector(http, page_size=2).fetch_company("x"))
    assert e.value.reason is ReasonCode.TRUNCATED


def test_lever_max_pages_guard(make_http: MakeHttp) -> None:
    postings = [{"id": str(i)} for i in range(10)]
    http, _ = make_http(lever_pages(postings))
    with pytest.raises(FetchError) as e:
        list(LeverConnector(http, page_size=2, max_pages=3).fetch_company("x"))
    assert e.value.reason is ReasonCode.TRUNCATED


def test_lever_unknown_board(make_http: MakeHttp) -> None:
    http, _ = make_http(lambda r: httpx.Response(404, json={"ok": False}))
    with pytest.raises(FetchError) as e:
        list(LeverConnector(http).fetch_company("nope"))
    assert e.value.reason is ReasonCode.BOARD_NOT_FOUND
