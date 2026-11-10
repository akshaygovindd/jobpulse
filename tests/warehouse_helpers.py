"""Helpers for warehouse tests: ingest synthetic postings on chosen dates, then run the pipeline."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from jobpulse.bronze.loader import BronzeResult, load_bronze
from jobpulse.config import WarehouseConfig, load_pipeline
from jobpulse.connectors.base import Page
from jobpulse.http import FetchError, ReasonCode
from jobpulse.ingest.runner import RunResult, run_ingest
from jobpulse.ingest.storage import Storage
from jobpulse.silver import SilverResult, build_silver
from jobpulse.warehouse import connect
from tests.conftest import TickingClock, employer

Jobs = Sequence[dict[str, Any]] | None  # None => the fetch fails (no pointer is written)


def gh_job(
    pid: str, title: str = "Data Engineer", content: str = "Build pipelines", loc: str = "NYC"
) -> dict[str, Any]:
    return {
        "id": pid,
        "title": title,
        "location": {"name": loc},
        "content": content,
        "absolute_url": f"https://example.test/gh/{pid}",
        "updated_at": "ignored",
    }


def ashby_job(
    pid: str, title: str = "Data Engineer", desc: str = "Build pipelines", loc: str = "NYC"
) -> dict[str, Any]:
    return {
        "id": pid,
        "title": title,
        "location": loc,
        "descriptionPlain": desc,
        "jobUrl": f"https://example.test/ashby/{pid}",
    }


class ScriptedConnector:
    def __init__(self, source_id: str, boards: Mapping[str, Jobs]) -> None:
        self.source_id = source_id
        self._boards = boards

    def fetch_company(self, board_id: str) -> Iterator[Page]:
        jobs = self._boards[board_id]
        if jobs is None:
            raise FetchError(ReasonCode.RATE_LIMITED, "scripted failure")
        payload: Any = list(jobs) if self.source_id == "lever" else {"jobs": list(jobs)}
        yield Page(1, payload, tuple(str(j["id"]) for j in jobs))


def ingest_day(
    storage: Storage,
    day: date,
    source: str,
    boards: Mapping[str, Jobs],
    *,
    attempt_id: str | None = None,
    companies: Mapping[str, str] | None = None,
    hour: int = 9,
) -> RunResult:
    """Run an ingest as if it happened on `day`. A later `hour` makes a later attempt."""
    clock = TickingClock(datetime(day.year, day.month, day.day, hour, 17, tzinfo=UTC))
    names = companies or {}
    return run_ingest(
        [employer(source, b, names.get(b, b.title())) for b in boards],
        {source: ScriptedConnector(source, boards)},
        storage,
        snapshot_date=day,
        attempt_id=attempt_id or f"local-{day.isoformat()}-1",
        env="local",
        clock=clock,
    )


def warehouse_cfg() -> WarehouseConfig:
    return load_pipeline().warehouse


def run_pipeline(storage: Storage, db_path: Path) -> tuple[BronzeResult, SilverResult]:
    cfg = warehouse_cfg()
    con = connect(cfg, db_path=db_path)
    clock = TickingClock(datetime(2026, 12, 1, 0, 0, tzinfo=UTC))
    bronze = load_bronze(storage, con, cfg, clock=clock, attempt_id="local-test")
    con.close()
    silver = build_silver(cfg, db_path=db_path)
    return bronze, silver


def query(db_path: Path, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(db_path))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def counts(db_path: Path) -> dict[str, int]:
    tables = [
        "bronze.raw_postings",
        "bronze.snapshot_pointers",
        "silver.dim_postings",
        "silver.fct_posting_observations",
        "silver.dim_posting_versions",
    ]
    return {t: int(query(db_path, f"SELECT count(*) FROM {t}")[0][0]) for t in tables}
