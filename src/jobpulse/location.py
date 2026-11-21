"""Place heuristic for gold: derive one place from free-text `location_raw`, or None.

A place is (city, state/province, country), so same-named cities in different places never merge:
"Dublin, CA" and "Dublin, Ireland" are two places. Deliberately conservative: a string with several
places, a remote-only string, or one that is only a region/country yields None rather than a guess.
A bare city ("Dublin") keeps whatever the text says; it only gains a state or country when the
city is listed in `known_places` (config/locations.yaml). All rules and word lists live there.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from jobpulse.config import LocationsConfig

_CODE_PREFIX = re.compile(r"^(?P<cc>[A-Z]{2})-(?P<rest>.+)$")  # "US-CA-Menlo Park", "GB-London"
_STATE_LEVEL_COUNTRIES = ("US", "CA")  # prefixes whose second token is a state/province code


@dataclass(frozen=True)
class Place:
    city: str
    state: str | None = None  # US state or Canadian province, canonical name
    country: str | None = None  # canonical name; implied by the state when there is one

    @property
    def key(self) -> str:
        """Stable identity of the place (lower-case city|state|country)."""
        return "|".join(
            (self.city.lower(), (self.state or "").lower(), (self.country or "").lower())
        )

    @property
    def label(self) -> str:
        """Display name, unique per key: "Dublin, California", "Dublin, Ireland", "Dublin"."""
        if self.state:
            return f"{self.city}, {self.state}"
        if self.country:
            return f"{self.city}, {self.country}"
        return self.city


class CityParser:
    def __init__(self, cfg: LocationsConfig) -> None:
        self._ignored = {s.lower() for s in cfg.ignored_locations}
        self._prefix = re.compile(cfg.workplace_prefix_pattern)
        self._remote = re.compile(cfg.remote_pattern)
        self._multi = re.compile(cfg.multi_pattern)
        self._noise = re.compile(cfg.noise_pattern)
        self._regions = {r.lower() for r in cfg.regions}
        self._also_cities = {c.lower() for c in cfg.also_cities}
        self._aliases = {k.lower(): v for k, v in cfg.aliases.items()}
        # lower-case alias -> (state, country) / country
        self._states: dict[str, tuple[str, str]] = {}
        for country, table in (("United States", cfg.us_states), ("Canada", cfg.ca_provinces)):
            for name, aliases in table.items():
                for a in (name.lower(), *aliases):
                    self._states[a.lower()] = (name, country)
        self._countries: dict[str, str] = {}
        for name, aliases in cfg.countries.items():
            for a in (name.lower(), *aliases):
                self._countries[a.lower()] = name
        self._canadian = set(cfg.ca_provinces)
        self._country_codes = {k.upper(): v for k, v in cfg.country_codes.items()}
        self._known = {k.lower(): v for k, v in cfg.known_places.items()}

    def parse(self, location_raw: str | None) -> Place | None:
        raw = self._prefix.sub("", (location_raw or "").strip(), count=1).strip()
        if not raw or raw.lower() in self._ignored:
            return None
        if self._remote.search(raw):
            return None
        m = _CODE_PREFIX.match(raw)
        if m and m.group("cc") in self._country_codes:
            return self._parse_prefixed(m.group("cc"), m.group("rest"))
        if self._multi.search(raw):
            return None
        cleaned = self._clean(raw)
        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        if not parts:
            return None
        first = parts[0].lower()
        if first in self._regions and first not in self._also_cities:
            return None
        # "Singapore, Singapore" / "New York, New York": a repeated name is not another place.
        if any(p.lower() not in self._regions and p.lower() != first for p in parts[1:]):
            return None  # "Toronto, Chicago, Atlanta": a list of places, not city + region
        city = self._canonical(parts[0])
        if city is None:
            return None
        state, country = self._resolve(city, parts[1:])
        return self._place(city, state, country)

    def _clean(self, text: str) -> str:
        return re.sub(r"\s+", " ", self._noise.sub(" ", text)).strip(" ,-")

    def _parse_prefixed(self, cc: str, rest: str) -> Place | None:
        tokens = [t.strip() for t in rest.split("-") if t.strip()]
        if not tokens:
            return None
        state: str | None = None
        country = self._country_codes[cc]
        if cc in _STATE_LEVEL_COUNTRIES:
            hit = self._states.get(tokens[0].lower())
            if hit is not None and hit[1] == country and len(tokens) >= 2:
                state, city_token = hit[0], tokens[1]  # "US-CA-Menlo Park", "CA-Ontario-Toronto"
            elif cc == "US":
                return None  # "US-XX-Foo": an unknown state code
            else:
                city_token = tokens[0]  # "CA-Toronto"
        else:
            city_token = tokens[0]  # "PL-Warsaw-Lixa C": anything after the city is an office
        city = self._canonical(self._clean(city_token))
        if city is None:
            return None
        return self._place(city, state, country)

    def _resolve(self, city: str, parts: Iterable[str]) -> tuple[str | None, str | None]:
        state: str | None = None
        implied: str | None = None
        explicit: str | None = None
        known = self._known.get(city.lower(), {})
        canadian = known.get("country") == "Canada" or known.get("state") in self._canadian
        for p in parts:
            low = p.lower()
            if low == "ca" and canadian:
                low = "canada"  # "Toronto, CA": the country code, not California
            if low == known.get("not_state") and known.get("country"):
                low = known["country"].lower()  # "Munich, DE": Germany's code, not Delaware
            if low in self._states and state is None:
                state, implied = self._states[low]
            elif low in self._countries and explicit is None:
                explicit = self._countries[low]
        return state, explicit or implied

    def _place(self, city: str, state: str | None, country: str | None) -> Place:
        known = self._known.get(city.lower())
        if known and state is None:
            kstate = known.get("state")
            if kstate is not None:
                kcountry = next(c for s, c in self._states.values() if s == kstate)
                if country is None or country == kcountry:
                    state, country = kstate, kcountry
            elif country is None:
                country = known.get("country")
        return Place(city=city, state=state, country=country)

    def _canonical(self, city: str) -> str | None:
        city = re.sub(r"\s+", " ", city).strip()
        low = city.lower()
        if not city or (low in self._regions and low not in self._also_cities):
            return None
        if low in self._aliases:
            return self._aliases[low]
        return city.title() if city.isupper() or city.islower() else city

    def parse_many(self, values: Iterable[str | None]) -> dict[str, Place | None]:
        return {v: self.parse(v) for v in values if v is not None}
