"""Read-only data access for the Streamlit app (app/).

The app reads the gold schema only. This module is the single door to the warehouse:
- a statement must be one SELECT/WITH query, with no write, DDL, attach, pragma or load keywords;
- every table it reads must be `gold.<name>` and `<name>` must be in GOLD_TABLES (the working
  tables in gold_stage and the bronze/silver/ops/extract/ref schemas are not reachable);
- the connection is opened read-only (local file) or with a MotherDuck read-scaling token.
Evidence from public filings, not a promise of sponsorship: nothing here changes that wording; the
pages carry it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb

from jobpulse.config import load_pipeline

GOLD_TABLES = frozenset(
    {
        "fct_postings",
        "dim_company",
        "dim_skill",
        "bridge_posting_skill",
        "agg_skill_demand_weekly",
        "agg_salary_by_role_city",
        "agg_lca_company_year",
        "agg_lca_company_role",
        "agg_lca_wage_level",
        "agg_lca_wage_by_soc_city",
        "fct_snapshot_freshness",
        "fct_pipeline_runs",
        "agg_ingest_status_daily",
        "agg_data_quality",
        "agg_llm_cost_daily",
    }
)
LOCAL_DB_ENV = "JOBPULSE_APP_DB"  # a local DuckDB file (dev, tests)
READ_TOKEN_ENV = "MOTHERDUCK_READ_TOKEN"  # MotherDuck read-scaling token (never a write token)

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|pragma|install|load|export|"
    r"import|call|set|reset|use|truncate|merge|replace|checkpoint|vacuum|read_csv|read_parquet|"
    r"read_json|read_text|glob)\b",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w.\"]*)", re.IGNORECASE)


class AppDataError(Exception):
    """The app cannot reach its data, or a statement was refused. The text is safe to show."""


def validate_statement(sql: str) -> str:
    """Return the statement if it is a single read-only query over allow-listed gold tables."""
    text = sql.strip().rstrip(";").strip()
    if not text or ";" in text:
        raise AppDataError("exactly one statement is allowed")
    if "--" in text or "/*" in text:
        raise AppDataError("comments are not allowed in app queries")
    if not re.match(r"(?i)^(select|with)\b", text):
        raise AppDataError("only SELECT queries are allowed")
    if _FORBIDDEN.search(re.sub(r"'(?:[^']|'')*'", "''", text)):  # ignore words inside literals
        raise AppDataError("the statement contains a keyword the app may not use")
    ctes = {m.lower() for m in re.findall(r"(?i)\b([A-Za-z_]\w*)\s+as\s*\(", text)}
    for ref in _TABLE_REF.findall(text):
        name = ref.replace('"', "")
        if name.lower() in ctes:
            continue
        schema, _, table = name.partition(".")
        if schema.lower() != "gold" or table not in GOLD_TABLES:
            raise AppDataError(f"table {name!r} is not an allowed gold table")
    return text


def connect(environ: Mapping[str, str] | None = None) -> duckdb.DuckDBPyConnection:
    """Open the gold data read-only: a local file when JOBPULSE_APP_DB is set, else MotherDuck."""
    env = os.environ if environ is None else environ
    local = env.get(LOCAL_DB_ENV)
    if local:
        path = Path(local)
        if not path.exists():
            raise AppDataError(f"{LOCAL_DB_ENV} points to a file that does not exist")
        return duckdb.connect(str(path), read_only=True)
    token = env.get(READ_TOKEN_ENV)
    if not token:
        raise AppDataError(f"set {LOCAL_DB_ENV} (a local file) or {READ_TOKEN_ENV} (MotherDuck)")
    database = load_pipeline().warehouse.database
    try:
        return duckdb.connect(f"md:{database}", config={"motherduck_token": token})
    except duckdb.Error as e:
        raise AppDataError(
            f"cannot connect to MotherDuck: {str(e).replace(token, '***')}"
        ) from None


def read_gold(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] | None = None) -> Any:
    """Run one validated read-only query and return a pandas DataFrame (pandas ships with
    Streamlit, so there is no new dependency)."""
    text = validate_statement(sql)
    try:
        return con.execute(text, list(params or [])).df()
    except duckdb.CatalogException:
        raise AppDataError(
            "the gold tables are not built yet (run `jobpulse build-gold`)"
        ) from None
    except duckdb.Error as e:
        raise AppDataError(f"query failed: {e}") from None
