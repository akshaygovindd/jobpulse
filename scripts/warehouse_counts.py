"""Print warehouse row counts (the source of every row-count figure quoted in the docs).

    uv run python scripts/warehouse_counts.py [--db path/to/local.duckdb]

Uses MotherDuck unless --db is given. Prints counts only; never prints connection details.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jobpulse.config import load_pipeline
from jobpulse.warehouse import WarehouseError, connect

TABLES = (
    ("ops", "pipeline_runs"),
    ("ops", "company_snapshots"),
    ("ops", "source_registry"),
    ("bronze", "snapshot_pointers"),
    ("bronze", "raw_postings"),
    ("silver", "dim_postings"),
    ("silver", "fct_posting_observations"),
    ("silver", "dim_posting_versions"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    args = parser.parse_args()
    cfg = load_pipeline().warehouse
    schemas = {"ops": cfg.ops_schema, "bronze": cfg.bronze_schema, "silver": cfg.silver_schema}
    try:
        con = connect(cfg, db_path=Path(args.db) if args.db else None)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for layer, table in TABLES:
        qualified = f'"{schemas[layer]}"."{table}"'
        try:
            row = con.execute(f"SELECT count(*) FROM {qualified}").fetchone()
            print(f"{layer}.{table}: {row[0] if row else 0}")
        except Exception:
            print(f"{layer}.{table}: (missing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
