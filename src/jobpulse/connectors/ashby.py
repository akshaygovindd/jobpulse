"""Ashby public job board API: GET /posting-api/job-board/{board} returns the whole board.

The response has no total count, so completeness cannot be cross-checked. The one failure we can
detect is an empty board: unless `allow_empty` is set, an empty `jobs` list is reported as
`truncated`, so a board that suddenly returns nothing is never read as "everything closed".
"""

from __future__ import annotations

from collections.abc import Iterator

from jobpulse.connectors.base import Page
from jobpulse.http import FetchError, HttpClient, ReasonCode

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyConnector:
    source_id = "ashby"

    def __init__(
        self, http: HttpClient, *, include_compensation: bool = True, allow_empty: bool = False
    ) -> None:
        self._http = http
        self._include_compensation = include_compensation
        self._allow_empty = allow_empty

    def fetch_company(self, board_id: str) -> Iterator[Page]:
        params = {"includeCompensation": "true"} if self._include_compensation else None
        payload = self._http.get_json(f"{BASE_URL}/{board_id}", params=params)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise FetchError(ReasonCode.PARSE_ERROR, "expected object with 'jobs' list")
        jobs = payload["jobs"]
        ids: list[str] = []
        for job in jobs:
            if not isinstance(job, dict) or "id" not in job:
                raise FetchError(ReasonCode.PARSE_ERROR, "job without id")
            ids.append(str(job["id"]))
        if len(set(ids)) != len(ids):
            raise FetchError(ReasonCode.PARSE_ERROR, "duplicate job ids in response")
        if not jobs and not self._allow_empty:
            raise FetchError(
                ReasonCode.TRUNCATED, "board returned 0 jobs; refusing to treat as complete"
            )
        yield Page(number=1, payload=payload, external_ids=tuple(ids))
