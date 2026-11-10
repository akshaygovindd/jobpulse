"""Live smoke tests against real public APIs. Excluded by default; run with `pytest -m live`."""

from __future__ import annotations

import pytest

from jobpulse.config import load_pipeline
from jobpulse.connectors.greenhouse import GreenhouseConnector
from jobpulse.connectors.lever import LeverConnector
from jobpulse.http import HttpClient

pytestmark = pytest.mark.live


def test_greenhouse_live() -> None:
    with HttpClient(load_pipeline().http) as http:
        pages = list(GreenhouseConnector(http, include_content=False).fetch_company("everquote"))
    assert pages[0].external_ids


def test_lever_live() -> None:
    with HttpClient(load_pipeline().http) as http:
        pages = list(LeverConnector(http, page_size=10).fetch_company("wealthfront"))
    assert sum(len(p.external_ids) for p in pages) > 0
