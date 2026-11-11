"""Print gold-layer figures (the source of every gold number quoted in the docs).

    uv run python scripts/gold_report.py [--db path/to/local.duckdb]

Read-only. Uses MotherDuck unless --db is given. Prints counts and percentages only; never prints
connection details.
"""

from __future__ import annotations

import argparse
import sys

from jobpulse.cli import _warehouse
from jobpulse.warehouse import WarehouseError

QUERIES: tuple[tuple[str, str], ...] = (
    ("fct_postings rows", "SELECT count(*) FROM {g}.fct_postings"),
    ("  active", "SELECT count(*) FROM {g}.fct_postings WHERE is_active"),
    ("  with extraction", "SELECT count(*) FROM {g}.fct_postings WHERE has_extraction"),
    (
        "  active, non-duplicate, extracted",
        "SELECT count(*) FROM {g}.fct_postings "
        "WHERE is_active AND has_extraction AND NOT is_probable_duplicate",
    ),
    (
        "  active postings with a parsed city (%)",
        "SELECT round(100.0 * count(city) / nullif(count(*), 0), 1) "
        "FROM {g}.fct_postings WHERE is_active",
    ),
    (
        "  extracted with advertised salary (%)",
        "SELECT round(100.0 * count(*) FILTER (WHERE has_advertised_salary) "
        "/ nullif(count(*), 0), 1) "
        "FROM {g}.fct_postings WHERE has_extraction",
    ),
    (
        "  with salary but no annualizable period (rows)",
        "SELECT count(*) FROM {g}.fct_postings "
        "WHERE has_advertised_salary AND salary_min_annual IS NULL AND salary_max_annual IS NULL",
    ),
    (
        "  salary period inferred (rows)",
        "SELECT count(*) FROM {g}.fct_postings WHERE salary_period_inferred",
    ),
    ("dim_company rows", "SELECT count(*) FROM {g}.dim_company"),
    ("dim_skill rows", "SELECT count(*) FROM {g}.dim_skill"),
    ("bridge_posting_skill rows", "SELECT count(*) FROM {g}.bridge_posting_skill"),
    ("agg_skill_demand_weekly rows", "SELECT count(*) FROM {g}.agg_skill_demand_weekly"),
    ("agg_salary_by_role_city rows", "SELECT count(*) FROM {g}.agg_salary_by_role_city"),
    ("snap_postings rows", "SELECT count(*) FROM {g}.snap_postings"),
    ("  current versions", "SELECT count(*) FROM {g}.snap_postings WHERE dbt_valid_to IS NULL"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    args = parser.parse_args()
    try:
        con = _warehouse(argparse.Namespace(db=args.db))
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    from jobpulse.config import load_pipeline

    g = f'"{load_pipeline().warehouse.gold_schema}"'
    for label, sql in QUERIES:
        row = con.execute(sql.format(g=g)).fetchone()
        print(f"{label}: {row[0] if row else 'n/a'}")
    size = con.execute("PRAGMA database_size").fetchone()
    if size:
        print(f"database_size: {size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
