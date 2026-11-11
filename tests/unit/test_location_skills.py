"""City heuristic and shared skill normalization (gold inputs)."""

from __future__ import annotations

import pytest

from jobpulse.config import load_locations
from jobpulse.location import CityParser
from jobpulse.skills import norm_skill

PARSER = CityParser(load_locations())


@pytest.mark.parametrize(
    ("raw", "city"),
    [
        ("Boston, MA", "Boston"),
        ("New York, NY", "New York"),
        ("New York, New York, United States", "New York"),
        ("New York City Office", "New York"),
        ("New York, NY (HQ)", "New York"),
        ("San Francisco HQ", "San Francisco"),
        ("Washington, DC", "Washington"),
        ("Singapore, Singapore", "Singapore"),
        ("Vancouver, BC", "Vancouver"),
        ("US-CA-Menlo Park", "Menlo Park"),
        ("Bengaluru, India", "Bengaluru"),
        ("Bangalore", "Bengaluru"),
        ("Dublin", "Dublin"),
    ],
)
def test_single_city(raw: str, city: str) -> None:
    assert PARSER.parse(raw) == city


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "United States",
        "Canada",
        "Remote - USA",
        "Remote",
        "US-DE-Remote",
        "N/A",
        "San Francisco, CA; New York, NY",
        "San Francisco, CA • New York, NY",
        "San Francisco or Seattle",
        "Toronto, Chicago, Atlanta",
        "Hybrid - New York, NY",
        "Texas, USA",
    ],
)
def test_no_single_city(raw: str | None) -> None:
    assert PARSER.parse(raw) is None


def test_norm_skill_collapses_variants() -> None:
    assert norm_skill("Apache Spark") == norm_skill("spark") == "spark"
    assert norm_skill("Natural Language Processing (NLP)") == "natural language processing"
    assert norm_skill("Data Pipelines") == norm_skill("data pipeline")
    assert norm_skill("SQL") == "sql"
    assert norm_skill("") == ""
