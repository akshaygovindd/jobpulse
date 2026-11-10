"""Per-source mapping from a raw API posting to the four fields that define its content.

The mapping lives here (not in SQL) so there is one tested definition per source. `content_hash`
covers only these fields, so volatile API fields (timestamps, counters) never create a new
version, while a real change to title, location, description or apply URL always does.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

_WS = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append(" ")


def html_to_text(value: str | None) -> str:
    """Strip tags and entities, collapse whitespace. Deterministic."""
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return _WS.sub(" ", "".join(parser.parts)).strip()


def _s(value: Any) -> str:
    return "" if value is None else _WS.sub(" ", str(value)).strip()


@dataclass(frozen=True)
class NormalizedPosting:
    external_posting_id: str
    title: str
    location_raw: str
    description_text: str
    apply_url: str

    @property
    def content_hash(self) -> str:
        parts = (self.title, self.location_raw, self.description_text, self.apply_url)
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class UnknownSourceError(ValueError):
    pass


def _greenhouse(job: dict[str, Any]) -> NormalizedPosting:
    # `content` is HTML that was itself HTML-escaped by the API: unescape once, then strip.
    location = job.get("location") or {}
    return NormalizedPosting(
        external_posting_id=str(job["id"]),
        title=_s(job.get("title")),
        location_raw=_s(location.get("name") if isinstance(location, dict) else location),
        description_text=html_to_text(html.unescape(job.get("content") or "")),
        apply_url=_s(job.get("absolute_url")),
    )


def _lever(job: dict[str, Any]) -> NormalizedPosting:
    categories = job.get("categories") or {}
    description = " ".join(
        part for part in (_s(job.get("descriptionPlain")), _s(job.get("additionalPlain"))) if part
    )
    return NormalizedPosting(
        external_posting_id=str(job["id"]),
        title=_s(job.get("text")),
        location_raw=_s(categories.get("location") if isinstance(categories, dict) else ""),
        description_text=description,
        apply_url=_s(job.get("hostedUrl") or job.get("applyUrl")),
    )


def _ashby(job: dict[str, Any]) -> NormalizedPosting:
    return NormalizedPosting(
        external_posting_id=str(job["id"]),
        title=_s(job.get("title")),
        location_raw=_s(job.get("location")),
        description_text=_s(job.get("descriptionPlain"))
        or html_to_text(job.get("descriptionHtml")),
        apply_url=_s(job.get("jobUrl") or job.get("applyUrl")),
    )


def normalize_posting(source_id: str, job: dict[str, Any]) -> NormalizedPosting:
    if source_id == "greenhouse":
        return _greenhouse(job)
    if source_id == "lever":
        return _lever(job)
    if source_id == "ashby":
        return _ashby(job)
    raise UnknownSourceError(f"no normalizer for source {source_id!r}")


def extract_jobs(source_id: str, page_payload: Any) -> list[dict[str, Any]]:
    """Return the posting dicts contained in one stored raw page."""
    jobs = page_payload if source_id == "lever" else page_payload.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
        raise ValueError(f"unexpected page shape for source {source_id!r}")
    return jobs
