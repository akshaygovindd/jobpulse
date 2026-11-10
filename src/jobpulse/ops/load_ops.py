"""Load the S3 ops JSON into the warehouse `ops` schema.

S3 stays the source of truth: these tables can always be rebuilt from `ops/`. The load is
idempotent. Per-attempt files (`pipeline_runs`, `company_snapshots`) are loaded once per attempt;
`source_registry` is current state and is replaced wholesale from `latest.json`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import duckdb

from jobpulse.config import WarehouseConfig
from jobpulse.ingest.keys import SOURCE_REGISTRY_LATEST
from jobpulse.ingest.storage import Storage, get_json

PIPELINE_RUN_COLUMNS = (
    "attempt_id",
    "snapshot_date",
    "task",
    "source_id",
    "started_at",
    "finished_at",
    "duration_s",
    "status",
    "companies_total",
    "companies_complete",
    "companies_failed",
    "rows_in",
    "rows_kept",
    "rows_dropped_json",
    "llm_cost_usd",
    "env",
)
COMPANY_SNAPSHOT_COLUMNS = (
    "attempt_id",
    "snapshot_date",
    "source_id",
    "board_id",
    "company",
    "status",
    "reason_code",
    "detail",
    "pages",
    "record_count",
    "manifest_key",
    "is_authoritative",
    "pointer_outcome",
    "started_at",
    "finished_at",
)
SOURCE_REGISTRY_COLUMNS = (
    "source_id",
    "source_name",
    "enabled",
    "attribution_url",
    "last_success_at",
    "last_failure_at",
    "last_cursor",
    "records_fetched",
)

_DDL = {
    "pipeline_runs": """
        attempt_id VARCHAR NOT NULL, snapshot_date DATE NOT NULL, task VARCHAR NOT NULL,
        source_id VARCHAR NOT NULL, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
        duration_s DOUBLE, status VARCHAR NOT NULL, companies_total INTEGER,
        companies_complete INTEGER, companies_failed INTEGER, rows_in BIGINT, rows_kept BIGINT,
        rows_dropped_json JSON, llm_cost_usd DOUBLE, env VARCHAR""",
    "company_snapshots": """
        attempt_id VARCHAR NOT NULL, snapshot_date DATE NOT NULL, source_id VARCHAR NOT NULL,
        board_id VARCHAR NOT NULL, company VARCHAR, status VARCHAR NOT NULL, reason_code VARCHAR,
        detail VARCHAR, pages INTEGER, record_count INTEGER, manifest_key VARCHAR,
        is_authoritative BOOLEAN, pointer_outcome VARCHAR, started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ""",
    "source_registry": """
        source_id VARCHAR NOT NULL, source_name VARCHAR, enabled BOOLEAN,
        attribution_url VARCHAR, last_success_at TIMESTAMPTZ, last_failure_at TIMESTAMPTZ,
        last_cursor VARCHAR, records_fetched BIGINT""",
}
_TIMESTAMPS = {"started_at", "finished_at", "last_success_at", "last_failure_at"}


@dataclass
class OpsLoadResult:
    pipeline_runs_loaded: int = 0
    company_snapshots_loaded: int = 0
    attempts_loaded: list[str] = field(default_factory=list)
    registry_rows: int = 0


def ensure_ops_tables(con: duckdb.DuckDBPyConnection, cfg: WarehouseConfig) -> None:
    for table, columns in _DDL.items():
        con.execute(f'CREATE TABLE IF NOT EXISTS "{cfg.ops_schema}"."{table}" ({columns})')


def _convert(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column == "snapshot_date":
        return date.fromisoformat(value) if isinstance(value, str) else value
    if column in _TIMESTAMPS and isinstance(value, str):
        return datetime.fromisoformat(value)
    if column == "rows_dropped_json":
        return json.dumps(value, sort_keys=True)
    return value


def insert_rows(
    con: duckdb.DuckDBPyConnection,
    cfg: WarehouseConfig,
    table: str,
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    cols = ", ".join(f'"{c}"' for c in columns)
    marks = ", ".join("?" for _ in columns)
    con.executemany(
        f'INSERT INTO "{cfg.ops_schema}"."{table}" ({cols}) VALUES ({marks})',
        [[_convert(c, row.get(c)) for c in columns] for row in rows],
    )


@contextmanager
def transaction(con: duckdb.DuckDBPyConnection) -> Iterator[None]:
    con.execute("BEGIN")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def record_pipeline_run(
    con: duckdb.DuckDBPyConnection, cfg: WarehouseConfig, row: dict[str, Any]
) -> None:
    """Idempotently write one pipeline_runs row (used by the bronze and silver steps)."""
    ensure_ops_tables(con, cfg)
    with transaction(con):
        con.execute(
            f'DELETE FROM "{cfg.ops_schema}"."pipeline_runs" '
            "WHERE attempt_id = ? AND task = ? AND source_id = ?",
            [row["attempt_id"], row["task"], row["source_id"]],
        )
        insert_rows(con, cfg, "pipeline_runs", PIPELINE_RUN_COLUMNS, [row])


def _attempt_id_from_key(key: str) -> str:
    return key.rsplit("attempt=", 1)[1].removesuffix(".json")


def _loaded_attempts(
    con: duckdb.DuckDBPyConnection, cfg: WarehouseConfig, table: str, where: str = ""
) -> set[str]:
    sql = f'SELECT DISTINCT attempt_id FROM "{cfg.ops_schema}"."{table}" {where}'
    return {r[0] for r in con.execute(sql).fetchall()}


def load_ops(
    storage: Storage, con: duckdb.DuckDBPyConnection, cfg: WarehouseConfig
) -> OpsLoadResult:
    ensure_ops_tables(con, cfg)
    result = OpsLoadResult()

    runs_done = _loaded_attempts(con, cfg, "pipeline_runs", "WHERE task = 'ingest'")
    for key in storage.list_keys("ops/pipeline_runs/"):
        attempt = _attempt_id_from_key(key)
        if attempt in runs_done:
            continue
        rows = get_json(storage, key) or []
        with transaction(con):
            insert_rows(con, cfg, "pipeline_runs", PIPELINE_RUN_COLUMNS, rows)
        result.pipeline_runs_loaded += len(rows)
        result.attempts_loaded.append(attempt)

    snaps_done = _loaded_attempts(con, cfg, "company_snapshots")
    for key in storage.list_keys("ops/company_snapshots/"):
        attempt = _attempt_id_from_key(key)
        if attempt in snaps_done:
            continue
        rows = get_json(storage, key) or []
        with transaction(con):
            insert_rows(con, cfg, "company_snapshots", COMPANY_SNAPSHOT_COLUMNS, rows)
        result.company_snapshots_loaded += len(rows)

    registry = get_json(storage, SOURCE_REGISTRY_LATEST)
    if registry is not None:
        with transaction(con):
            con.execute(f'DELETE FROM "{cfg.ops_schema}"."source_registry"')
            insert_rows(con, cfg, "source_registry", SOURCE_REGISTRY_COLUMNS, registry)
        result.registry_rows = len(registry)
    return result
