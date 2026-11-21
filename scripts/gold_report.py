"""Print gold-layer figures (the source of every gold number quoted in the docs).

    uv run python scripts/gold_report.py [--db path/to/local.duckdb]

Read-only. Uses MotherDuck unless --db is given. Prints counts and percentages only; never prints
connection details.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

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
    (
        "  analyzed (target-role) postings with an apply link (%)",
        "SELECT round(100.0 * count(apply_url) / nullif(count(*), 0), 1) "
        "FROM {g}.fct_postings WHERE is_active AND has_extraction AND NOT is_probable_duplicate",
    ),
    (
        "  all open postings with an apply link (%)",
        "SELECT round(100.0 * count(apply_url) / nullif(count(*), 0), 1) "
        "FROM {g}.fct_postings WHERE is_active",
    ),
    (
        "  latest day: boards complete / failed (distinct companies)",
        "SELECT snapshot_date::varchar || ': ' || "
        "coalesce(sum(n_authoritative_companies), 0)::varchar "
        "|| ' complete, ' "
        "|| coalesce(sum(n_companies) FILTER (WHERE status <> 'complete'), 0)::varchar "
        "|| ' not complete' FROM {g}.agg_ingest_status_daily "
        "WHERE snapshot_date = (SELECT max(snapshot_date) FROM {g}.agg_ingest_status_daily) "
        "GROUP BY snapshot_date",
    ),
    (
        "  complete days (all boards authoritative) / days observed",
        "SELECT count(*) FILTER (WHERE c >= 43)::varchar || ' / ' || count(*)::varchar FROM ("
        "SELECT snapshot_date, sum(n_authoritative_companies) c "
        "FROM {g}.agg_ingest_status_daily GROUP BY 1)",
    ),
    ("dim_company rows", "SELECT count(*) FROM {g}.dim_company"),
    (
        "companies with certified H-1B filings (FY2025-FY2026)",
        "SELECT count(*) FROM {g}.dim_company WHERE n_lca_certified > 0",
    ),
    (
        "certified H-1B filings matched to our companies",
        "SELECT sum(n_lca_certified) FROM {g}.dim_company",
    ),
    ("dim_skill rows", "SELECT count(*) FROM {g}.dim_skill"),
    ("bridge_posting_skill rows", "SELECT count(*) FROM {g}.bridge_posting_skill"),
    ("agg_skill_demand_weekly rows", "SELECT count(*) FROM {g}.agg_skill_demand_weekly"),
    ("agg_salary_by_role_city rows", "SELECT count(*) FROM {g}.agg_salary_by_role_city"),
    ("snap_postings rows", "SELECT count(*) FROM {g}.snap_postings"),
    ("  current versions", "SELECT count(*) FROM {g}.snap_postings WHERE dbt_valid_to IS NULL"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    args = parser.parse_args(argv)
    try:
        con = _warehouse(argparse.Namespace(db=args.db))
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    from jobpulse.config import load_pipeline

    schema = load_pipeline().warehouse.gold_schema
    g = f'"{schema}"'
    built = con.execute(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = ?", [schema]
    ).fetchone()
    if not built or not built[0]:
        print(f"gold schema '{schema}' does not exist yet: run `jobpulse build-gold` first")
    else:
        for label, sql in QUERIES:
            try:
                row = con.execute(sql.format(g=g)).fetchone()
            except duckdb.CatalogException:
                print(f"{label}: table not built yet")
                continue
            print(f"{label}: {row[0] if row else 'n/a'}")
    size = con.execute("PRAGMA database_size").fetchone()
    if size and size[2]:  # MotherDuck reports 0 bytes and no block size
        print(f"database_size: {size[1]}")
    else:
        print("database_size: not reported here (run scripts/storage_projection.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
