"""Lever Postings API: GET /v0/postings/{board}?mode=json&skip=&limit= (offset pagination)."""

from __future__ import annotations

from collections.abc import Iterator

from jobpulse.connectors.base import Page
from jobpulse.http import FetchError, HttpClient, ReasonCode

BASE_URL = "https://api.lever.co/v0/postings"


class LeverConnector:
    source_id = "lever"

    def __init__(self, http: HttpClient, *, page_size: int = 100, max_pages: int = 50) -> None:
        self._http = http
        self._page_size = page_size
        self._max_pages = max_pages

    def fetch_company(self, board_id: str) -> Iterator[Page]:
        seen: set[str] = set()
        for number in range(1, self._max_pages + 1):
            skip = (number - 1) * self._page_size
            payload = self._http.get_json(
                f"{BASE_URL}/{board_id}",
                params={"mode": "json", "skip": skip, "limit": self._page_size},
            )
            if not isinstance(payload, list):
                raise FetchError(ReasonCode.PARSE_ERROR, "expected a JSON list of postings")
            ids: list[str] = []
            for posting in payload:
                if not isinstance(posting, dict) or "id" not in posting:
                    raise FetchError(ReasonCode.PARSE_ERROR, "posting without id")
                pid = str(posting["id"])
                if pid in seen:
                    # Offset pagination shifted under us; we may have skipped postings.
                    raise FetchError(
                        ReasonCode.TRUNCATED,
                        f"pagination drift: id {pid} repeated on page {number}",
                    )
                seen.add(pid)
                ids.append(pid)
            if payload or number == 1:
                yield Page(number=number, payload=payload, external_ids=tuple(ids))
            if len(payload) < self._page_size:
                return
        raise FetchError(ReasonCode.TRUNCATED, f"exceeded max_pages={self._max_pages}")
