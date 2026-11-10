"""Connector protocol.

Completeness contract: `fetch_company` yields pages and **returns normally only when the
connector has positively established that it fetched every posting** for the board. Anything
else (HTTP failure after retries, truncation, pagination drift, bad payload) raises
`jobpulse.http.FetchError` with a reason code. Callers treat any exception as "incomplete".
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Page:
    number: int  # 1-based
    payload: Any  # the raw API response for this page, preserved as-is
    external_ids: tuple[str, ...]  # posting ids contained in this page


class Connector(Protocol):
    source_id: str

    def fetch_company(self, board_id: str) -> Iterator[Page]: ...
