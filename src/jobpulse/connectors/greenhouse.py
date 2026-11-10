"""Greenhouse Job Board API: GET /v1/boards/{board}/jobs returns the whole board in one response."""

from __future__ import annotations

from collections.abc import Iterator

from jobpulse.connectors.base import Page
from jobpulse.http import FetchError, HttpClient, ReasonCode

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseConnector:
    source_id = "greenhouse"

    def __init__(self, http: HttpClient, *, include_content: bool = True) -> None:
        self._http = http
        self._include_content = include_content

    def fetch_company(self, board_id: str) -> Iterator[Page]:
        params = {"content": "true"} if self._include_content else None
        payload = self._http.get_json(f"{BASE_URL}/{board_id}/jobs", params=params)
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
        total = (payload.get("meta") or {}).get("total")
        if isinstance(total, int) and total != len(jobs):
            raise FetchError(
                ReasonCode.TRUNCATED, f"meta.total={total} but received {len(jobs)} jobs"
            )
        yield Page(number=1, payload=payload, external_ids=tuple(ids))
