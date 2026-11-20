"""Warehouse size and projected monthly growth (the source of every storage figure in the docs).

    uv run python scripts/storage_projection.py [--db FILE] [--marginal N] [--limit-gib 10]

Without --db it reads MotherDuck (read-only) and prints the used size and bronze row counts.

`--marginal N` (local file only) measures the real cost of a day: it copies the database to a temp
directory, then N+1 times rebuilds silver and gold, checkpoints and records the used size, removing
the newest bronze day between steps. The size difference between consecutive steps is what one more
day of data costs the whole warehouse (bronze + silver + gold), with the same code that runs daily.
The projection is mean(delta) x 30, compared with the plan's storage limit. It assumes today's
posting volume and change rate continue; it is an estimate, not a guarantee.

On MotherDuck `PRAGMA database_size` reports nothing useful (0 bytes, NULL block size), so the size
comes from `md_information_schema.storage_info`: active bytes (the live data), historical bytes
(kept for the database's snapshot retention, 7 days by default) and failsafe bytes. Which of these a
plan's storage limit counts is the plan's rule, not something this script can tell; all are shown.

For a local file, "used" is used_blocks x block_size from PRAGMA database_size. The `database_size`
column also counts free blocks, which a local file keeps after deletes, so it can be much larger
than the data.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb

from jobpulse.bronze.loader import ensure_bronze_tables
from jobpulse.cli import _warehouse
from jobpulse.config import load_pipeline
from jobpulse.gold import build_gold
from jobpulse.silver import build_silver
from jobpulse.warehouse import WarehouseError

MIB = 1024 * 1024


def md_storage(con: duckdb.DuckDBPyConnection, database: str) -> dict[str, int] | None:
    """Storage components for a MotherDuck database, or None if the view is unavailable."""
    try:
        row = con.execute(
            "SELECT active_bytes, historical_bytes, failsafe_bytes, retained_for_clone_bytes "
            "FROM md_information_schema.storage_info "
            "WHERE database_name = ? AND deleted_ts IS NULL",
            [database],
        ).fetchone()
    except duckdb.Error:
        return None
    if row is None:
        return None
    keys = ("active", "historical", "failsafe", "clone")
    return {k: int(v or 0) for k, v in zip(keys, row, strict=True)}


def used_bytes(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("CHECKPOINT")
    row = con.execute("PRAGMA database_size").fetchone()
    assert row is not None
    return int(row[4]) * int(row[2])  # used_blocks x block_size


def bronze_counts(con: duckdb.DuckDBPyConnection, b: str) -> tuple[int, int, int, str, str]:
    days, first, last = con.execute(
        "SELECT count(DISTINCT snapshot_date), min(snapshot_date), max(snapshot_date) "
        f'FROM "{b}"."raw_postings"'
    ).fetchone()  # type: ignore[misc]
    obs = con.execute(f'SELECT count(*) FROM "{b}"."raw_postings"').fetchone()[0]  # type: ignore[index]
    return int(days), int(obs), int(first is not None), str(first), str(last)


def drop_newest_day(con: duckdb.DuckDBPyConnection, b: str) -> str:
    day = con.execute(f'SELECT max(snapshot_date) FROM "{b}"."raw_postings"').fetchone()[0]  # type: ignore[index]
    con.execute(f'DELETE FROM "{b}"."raw_postings" WHERE snapshot_date = ?', [day])
    con.execute(f'DELETE FROM "{b}"."snapshot_pointers" WHERE snapshot_date = ?', [day])
    con.execute(
        f'DELETE FROM "{b}"."posting_content" c '
        f'WHERE NOT EXISTS (SELECT 1 FROM "{b}"."raw_postings" r '
        "WHERE r.source_id = c.source_id AND r.external_posting_id = c.external_posting_id "
        "AND r.content_hash = c.content_hash)"
    )
    return str(day)


def marginal(db: Path, steps: int, limit_gib: float) -> int:
    pipeline = load_pipeline()
    cfg = pipeline.warehouse
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "copy.duckdb"
        shutil.copy(db, work)
        con = duckdb.connect(str(work))
        ensure_bronze_tables(con, cfg)  # migrates an old-shape copy; never touches the original
        con.close()
        sizes: list[tuple[str, int]] = []
        for step in range(steps + 1):
            build_silver(cfg, db_path=work)
            build_gold(pipeline, db_path=work)
            con = duckdb.connect(str(work))
            newest = str(
                con.execute(
                    f'SELECT max(snapshot_date) FROM "{cfg.bronze_schema}"."raw_postings"'
                ).fetchone()[0]  # type: ignore[index]
            )
            sizes.append((newest, used_bytes(con)))
            if step < steps:
                drop_newest_day(con, cfg.bronze_schema)
            con.close()
    deltas = [(sizes[i][0], sizes[i][1] - sizes[i + 1][1]) for i in range(steps)]
    for day, d in deltas:
        print(f"cost of {day}: {d / MIB:.2f} MiB")
    mean = sum(d for _, d in deltas) / len(deltas)
    limit = limit_gib * 1024 * MIB
    print(f"mean cost of one day: {mean / MIB:.2f} MiB")
    month = mean * 30
    print(
        f"projected growth: {month / MIB:.0f} MiB per 30 days = "
        f"{100 * month / limit:.1f}% of the {limit_gib:g} GiB limit"
    )
    left = (limit - sizes[0][1]) / mean
    print(f"days until the limit from {sizes[0][1] / MIB:.0f} MiB used: {left:.0f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    parser.add_argument("--marginal", type=int, metavar="N", help="measure the last N days (local)")
    parser.add_argument("--limit-gib", type=float, default=10.0, help="storage limit of the plan")
    args = parser.parse_args(argv)
    if args.marginal and not args.db:
        print(
            "error: --marginal needs --db (it works on a temporary copy of a local file)",
            file=sys.stderr,
        )
        return 2
    try:
        con = _warehouse(argparse.Namespace(db=args.db, env="prod"))
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    b = load_pipeline().warehouse.bronze_schema
    row = con.execute("PRAGMA database_size").fetchone()
    md = None if args.db else md_storage(con, load_pipeline().warehouse.database)
    if md is not None:
        total = md["active"] + md["historical"] + md["failsafe"] + md["clone"]
        for k, v in md.items():
            print(f"{k + ':':<11}{v / MIB:8.1f} MiB")
        limit = args.limit_gib * 1024 * MIB
        print(
            f"total (all components): {total / MIB:.1f} MiB = {100 * total / limit:.1f}% of limit"
        )
        print(f"active only: {100 * md['active'] / limit:.1f}% of the {args.limit_gib:g} GiB limit")
    elif row is not None and row[2]:
        print(f"database_size (as reported): {row[1]}")
        print(
            f"used: {int(row[4]) * int(row[2]) / MIB:.1f} MiB ({row[4]} blocks of {row[2]} bytes)"
        )
    else:
        print("size: not available (no block size reported and no md_information_schema)")
    days, obs, _, first, last = bronze_counts(con, b)
    print(f"bronze: {obs} observation rows over {days} days ({first} to {last})")
    try:
        content = con.execute(f'SELECT count(*) FROM "{b}"."posting_content"').fetchone()[0]  # type: ignore[index]
        print(f"bronze posting_content rows: {content}")
    except duckdb.CatalogException:
        print("bronze posting_content: not created yet (old-shape bronze; run load-bronze)")
    con.close()
    if args.marginal:
        return marginal(Path(args.db), args.marginal, args.limit_gib)
    return 0


if __name__ == "__main__":
    sys.exit(main())
