"""City heuristic for gold: derive a single city from free-text `location_raw`, or None.

Deliberately conservative. A string with several places, a remote-only string, or one that is only a
region/country yields None rather than a guess. Rules and word lists live in config/locations.yaml.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from jobpulse.config import LocationsConfig

_US_PREFIX = re.compile(r"^US-[A-Za-z]{2}-(?P<city>[^,;]+)$")  # e.g. "US-CA-Menlo Park"


class CityParser:
    def __init__(self, cfg: LocationsConfig) -> None:
        self._remote = re.compile(cfg.remote_pattern)
        self._multi = re.compile(cfg.multi_pattern)
        self._noise = re.compile(cfg.noise_pattern)
        self._regions = {r.lower() for r in cfg.regions}
        self._also_cities = {c.lower() for c in cfg.also_cities}
        self._aliases = {k.lower(): v for k, v in cfg.aliases.items()}

    def parse(self, location_raw: str | None) -> str | None:
        raw = (location_raw or "").strip()
        if not raw:
            return None
        if self._remote.search(raw):
            return None
        m = _US_PREFIX.match(raw)
        if m:
            return self._canonical(m.group("city"))
        if self._multi.search(raw):
            return None
        cleaned = re.sub(r"\s+", " ", self._noise.sub(" ", raw)).strip(" ,-")
        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        if not parts:
            return None
        first = parts[0].lower()
        if first in self._regions and first not in self._also_cities:
            return None
        # "Singapore, Singapore" / "New York, New York": a repeated name is not another place.
        if any(p.lower() not in self._regions and p.lower() != first for p in parts[1:]):
            return None  # "Toronto, Chicago, Atlanta": a list of places, not city + region
        return self._canonical(parts[0])

    def _canonical(self, city: str) -> str | None:
        city = re.sub(r"\s+", " ", city).strip()
        low = city.lower()
        if not city or (low in self._regions and low not in self._also_cities):
            return None
        return self._aliases.get(city.lower(), city)

    def parse_many(self, values: Iterable[str | None]) -> dict[str, str | None]:
        return {v: self.parse(v) for v in values if v is not None}
