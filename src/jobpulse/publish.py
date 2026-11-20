"""Export the gold layer to one DuckDB file for the public app.

Only the tables in `app_data.GOLD_TABLES` are copied, into a fresh file that has a `gold` schema and
nothing else, so bronze, silver, ops, extraction cache and `ref` data can never reach the public
file. The export is validated (exactly the allow-listed tables, row counts equal to the source,
a minimum size) and moved into place atomically; on any failure the previous file is left alone, so
yesterday's published asset stays in place.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import duckdb

from jobpulse.app_data import GOLD_TABLES
from jobpulse.config import PublishConfig


class PublishError(Exception):
    """The export was refused. The text is safe to print."""


def _count(con: duckdb.DuckDBPyConnection, qualified: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {qualified}").fetchone()
    return int(row[0]) if row else 0


def export_gold(
    source: duckdb.DuckDBPyConnection,
    out: Path,
    cfg: PublishConfig,
    *,
    gold_schema: str = "gold",
) -> dict[str, int]:
    """Copy the allow-listed gold tables from `source` into `out`. Returns rows per table."""
    if "'" in str(out):
        raise PublishError("the output path may not contain a single quote")
    row = source.execute("SELECT current_database()").fetchone()
    catalog = str(row[0]) if row else ""  # qualify: the export also has a schema called "gold"
    present = {
        r[0]
        for r in source.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog = ? AND table_schema = ?",
            [catalog, gold_schema],
        ).fetchall()
    }
    missing = sorted(GOLD_TABLES - present)
    if missing:
        raise PublishError(f"gold tables missing in the source: {', '.join(missing)}")

    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    expected: dict[str, int] = {}
    attached = False
    try:
        source.execute(f"ATTACH '{tmp}' AS pub_export")
        attached = True
        source.execute('CREATE SCHEMA pub_export."gold"')
        for table in sorted(GOLD_TABLES):
            source.execute(
                f'CREATE TABLE pub_export."gold"."{table}" '
                f'AS SELECT * FROM "{catalog}"."{gold_schema}"."{table}"'
            )
            expected[table] = _count(source, f'"{catalog}"."{gold_schema}"."{table}"')
        source.execute("DETACH pub_export")
        attached = False
        _validate(tmp, expected, cfg)
        os.replace(tmp, out)
    except duckdb.Error as e:
        raise PublishError(f"export failed: {type(e).__name__}: {str(e)[:200]}") from None
    finally:
        if attached:
            with contextlib.suppress(duckdb.Error):
                source.execute("DETACH pub_export")
        tmp.unlink(missing_ok=True)
    return expected


def _validate(path: Path, expected: dict[str, int], cfg: PublishConfig) -> None:
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{path}' AS pub_check (READ_ONLY)")
        found = {
            (r[0], r[1])
            for r in con.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_catalog = 'pub_check' "
                "AND table_schema NOT IN ('information_schema', 'pg_catalog')"
            ).fetchall()
        }
        want = {("gold", t) for t in GOLD_TABLES}
        if found != want:
            raise PublishError(f"unexpected contents: {sorted(found ^ want)}")
        for table, n in expected.items():
            got = _count(con, f'pub_check."gold"."{table}"')
            if got != n:
                raise PublishError(f"{table}: {got} rows in the export, {n} in the source")
        for table, floor in cfg.export.min_rows.items():
            if expected.get(table, 0) < floor:
                raise PublishError(f"{table} has {expected.get(table, 0)} rows, below {floor}")
    finally:
        con.close()
