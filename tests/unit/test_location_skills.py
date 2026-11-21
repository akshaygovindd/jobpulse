"""City heuristic and shared skill normalization (gold inputs)."""

from __future__ import annotations

import pytest

from jobpulse.config import load_locations
from jobpulse.gold import unify_spellings
from jobpulse.location import CityParser
from jobpulse.skills import norm_skill

PARSER = CityParser(load_locations())


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("Boston, MA", "Boston, Massachusetts"),
        ("Boston, Massachusetts, United States", "Boston, Massachusetts"),
        ("Boston", "Boston, Massachusetts"),
        ("New York, NY", "New York, New York"),
        ("New York, New York, United States", "New York, New York"),
        ("New York City Office", "New York, New York"),
        ("New York, NY (HQ)", "New York, New York"),
        ("US-NY-New York", "New York, New York"),
        ("San Francisco HQ", "San Francisco, California"),
        ("San Francisco, California, United States", "San Francisco, California"),
        ("Washington, DC", "Washington, District of Columbia"),
        ("Washington D.C.", "Washington, District of Columbia"),
        ("Seattle, Washington, United States", "Seattle, Washington"),
        ("Singapore, Singapore", "Singapore, Singapore"),
        ("Singapore", "Singapore, Singapore"),
        ("SG-Singapore", "Singapore, Singapore"),
        ("Vancouver, BC", "Vancouver, British Columbia"),
        ("Vancouver, Canada", "Vancouver, British Columbia"),
        ("Toronto, Ontario, Canada", "Toronto, Ontario"),
        ("Toronto, CA", "Toronto, Ontario"),
        ("Toronto, ON, CA", "Toronto, Ontario"),
        ("Washington DC", "Washington, District of Columbia"),
        ("Toronto", "Toronto, Ontario"),
        ("CA-Ontario-Toronto", "Toronto, Ontario"),
        ("US-CA-Menlo Park", "Menlo Park, California"),
        ("Bengaluru, India", "Bengaluru, India"),
        ("Bangalore", "Bengaluru, India"),
        ("London, United Kingdom", "London, United Kingdom"),
        ("London, England", "London, United Kingdom"),
        ("London, UK", "London, United Kingdom"),
        ("GB-London", "London, United Kingdom"),
        ("London", "London, United Kingdom"),
        ("London, London, United Kingdom", "London, United Kingdom"),
        ("PL-Warsaw-Lixa C", "Warsaw, Poland"),
        ("Tokyo, Japan", "Tokyo, Japan"),
        ("JP-Tokyo", "Tokyo, Japan"),
    ],
)
def test_place_label(raw: str, label: str) -> None:
    place = PARSER.parse(raw)
    assert place is not None
    assert place.label == label


def test_same_name_different_places_never_merge() -> None:
    ireland = PARSER.parse("Dublin, Ireland")
    california = PARSER.parse("Dublin, CA")
    prefixed = PARSER.parse("US-CA-Dublin")
    bare = PARSER.parse("Dublin")  # owner decision: a bare Dublin is Dublin, Ireland
    assert ireland is not None
    assert california is not None
    assert prefixed is not None
    assert bare is not None
    assert bare.key == ireland.key
    assert ireland.key != california.key
    assert prefixed.key == california.key
    assert (ireland.label, california.label, bare.label) == (
        "Dublin, Ireland",
        "Dublin, California",
        "Dublin, Ireland",
    )
    assert PARSER.parse("IE-Dublin-WW") == ireland
    # Same state and city name, different state or country elsewhere:
    assert PARSER.parse("Portland, OR") != PARSER.parse("Portland, ME")
    assert PARSER.parse("London, Ontario, Canada") != PARSER.parse("London, UK")
    assert PARSER.parse("Vancouver, WA") != PARSER.parse("Vancouver, BC")


def test_ca_is_california_unless_the_city_is_canadian() -> None:
    assert PARSER.parse("Dublin, CA") == PARSER.parse("Dublin, California")
    place = PARSER.parse("Palo Alto, CA")
    assert place is not None
    assert place.state == "California"


def test_unify_spellings_gives_one_label_per_key() -> None:
    raws = {"Redwood City, CA": 5, "Redwood CIty, CA": 1, "Boston, MA": 2}
    places = unify_spellings({r: PARSER.parse(r) for r in raws}, raws)
    assert places["Redwood CIty, CA"] == places["Redwood City, CA"]
    place = places["Redwood CIty, CA"]
    assert place is not None
    assert place.label == "Redwood City, California"


def test_one_key_one_label() -> None:
    raws = [
        "Boston, MA", "Boston", "Dublin", "Dublin, CA", "Dublin, Ireland", "London", "London, UK",
        "Toronto", "Toronto, Canada", "Washington, DC", "Washington, Washington",
    ]  # fmt: skip
    seen: dict[str, str] = {}
    for raw in raws:
        place = PARSER.parse(raw)
        assert place is not None
        assert seen.setdefault(place.key, place.label) == place.label
    assert len(set(seen.values())) == len(seen)


def test_locations_config_is_consistent() -> None:
    cfg = load_locations()
    known_words = {r.lower() for r in cfg.regions} | {c.lower() for c in cfg.also_cities}
    for table in (cfg.us_states, cfg.ca_provinces, cfg.countries):
        for aliases in table.values():
            assert set(aliases) <= known_words
    state_names = {*cfg.us_states, *cfg.ca_provinces}
    for city, fill in cfg.known_places.items():
        assert set(fill) in ({"state"}, {"country"}, {"country", "not_state"}), city
        if "state" in fill:
            assert fill["state"] in state_names, city
        else:
            assert fill["country"] in cfg.countries, city
    assert set(cfg.country_codes.values()) <= set(cfg.countries)


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


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("LOCATION", None),
        ("na", None),
        ("AMER", None),
        ("USCA", None),
        ("SEA", None),
        ("Asia Pacific", None),
        ("US Hybrid", None),
        ("Costa Rica", None),
        ("Chicago, New York", None),
        ("Fins Only-AU-Sydney", None),
        ("NYC-Privy", "New York, New York"),
        ("San Francsico", "San Francisco, California"),
        ("Munich, DE", "Munich, Germany"),
        ("FI-Helsinki-MSO", "Helsinki, Finland"),
        ("Austin", "Austin, Texas"),
        ("Austin, Texas", "Austin, Texas"),
        ("Denver", "Denver, Colorado"),
    ],
)
def test_junk_and_misspelled_locations_from_the_qa_audit(raw: str, label: str | None) -> None:
    place = PARSER.parse(raw)
    assert (place.label if place else None) == label


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("Hybrid - New York, NY", "New York, New York"),
        ("Remote - New York, NY", "New York, New York"),
        ("On-site - Boston, MA", "Boston, Massachusetts"),
        ("Onsite – Austin, TX", "Austin, Texas"),
        ("hybrid: Toronto, ON", "Toronto, Ontario"),
    ],
)
def test_workplace_prefix_is_stripped_before_the_city_is_parsed(raw: str, label: str) -> None:
    """QA: Coinbase "People Analytics Intern" ("Hybrid - New York, NY") had no city."""
    place = PARSER.parse(raw)
    assert place is not None
    assert place.label == label


@pytest.mark.parametrize(
    "raw",
    ["Remote - United States", "Remote - Anywhere", "Hybrid -", "Remote", "Hybrid - NYC / SF"],
)
def test_a_workplace_prefix_never_invents_a_city(raw: str) -> None:
    assert PARSER.parse(raw) is None
