"""Read-only data access for the Streamlit app (app/).

The app reads the gold schema only. This module is the single door to the warehouse:
- a statement must be one SELECT/WITH query, with no write, DDL, attach, pragma or load keywords;
- every table it reads must be `gold.<name>` and `<name>` must be in GOLD_TABLES (the working
  tables in gold_stage and the bronze/silver/ops/extract/ref schemas are not reachable);
- the connection is opened read-only: a local file, the published gold file (the public deploy;
  no token of any kind), or, for development only, a MotherDuck read-scaling token.
Evidence from public filings, not a promise of sponsorship: nothing here changes that wording; the
pages carry it.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import httpx

from jobpulse.config import PublishConfig, load_pipeline, load_publish
from jobpulse.http import FetchError, HttpClient

GOLD_TABLES = frozenset(
    {
        "fct_postings",
        "dim_company",
        "dim_skill",
        "bridge_posting_skill",
        "agg_skill_demand_weekly",
        "agg_salary_by_role",
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
GOLD_URL_ENV = "JOBPULSE_GOLD_URL"  # the published gold file (public deploy; no token at all)

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
    """Open the gold data read-only: a local file when JOBPULSE_APP_DB is set, else the published
    gold file when JOBPULSE_GOLD_URL is set, else MotherDuck with a read-scaling token (dev)."""
    env = os.environ if environ is None else environ
    local = env.get(LOCAL_DB_ENV)
    if local:
        path = Path(local)
        if not path.exists():
            raise AppDataError(f"{LOCAL_DB_ENV} points to a file that does not exist")
        return duckdb.connect(str(path), read_only=True)
    url = env.get(GOLD_URL_ENV)
    if url:
        return connect_published(url)
    token = env.get(READ_TOKEN_ENV)
    if not token:
        raise AppDataError(
            f"set {LOCAL_DB_ENV} (a local file), {GOLD_URL_ENV} (the published gold file) "
            f"or {READ_TOKEN_ENV} (MotherDuck, development only)"
        )
    database = load_pipeline().warehouse.database
    try:
        return duckdb.connect(f"md:{database}", config={"motherduck_token": token})
    except duckdb.Error as e:
        raise AppDataError(
            f"cannot connect to MotherDuck: {str(e).replace(token, '***')}"
        ) from None


def published_path(cfg: PublishConfig) -> Path:
    return Path(tempfile.gettempdir()) / cfg.app.cache_dir_name / cfg.release.asset


def _is_gold_only(path: Path) -> bool:
    """True when the file is a DuckDB database holding exactly the allow-listed gold tables."""
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return False
    try:
        rows = con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
        ).fetchall()
    except duckdb.Error:
        return False
    finally:
        con.close()
    return {(r[0], r[1]) for r in rows} == {("gold", t) for t in GOLD_TABLES}


def connect_published(
    url: str,
    *,
    cfg: PublishConfig | None = None,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> duckdb.DuckDBPyConnection:
    """Open the published gold file read-only, downloading it when the local copy is missing or
    older than `app.refresh_s`. A failed or invalid download keeps the previous copy; with no copy
    at all the app reports that its data is unavailable. No credential is involved."""
    cfg = cfg or load_publish()
    if not url.startswith(cfg.release.url_prefix):
        raise AppDataError(f"{GOLD_URL_ENV} must start with {cfg.release.url_prefix}")
    path = published_path(cfg)
    fresh = path.exists() and clock() - path.stat().st_mtime < cfg.app.refresh_s
    if not fresh:
        try:
            _download(url, path, cfg, transport)
        except (FetchError, OSError, AppDataError):
            if not path.exists():
                raise AppDataError("the published data could not be downloaded") from None
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        raise AppDataError("the published data file could not be opened") from None


def _download(
    url: str, path: Path, cfg: PublishConfig, transport: httpx.BaseTransport | None
) -> None:
    with HttpClient(load_pipeline().http, transport=transport) as http:
        data = http.request(
            "GET",
            url,
            headers={"Accept": "application/octet-stream"},
            timeout=120.0,
            expect="bytes",
        )
    if len(data) > cfg.app.max_download_bytes:
        raise AppDataError("the published file is larger than expected")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    if not _is_gold_only(part):
        part.unlink(missing_ok=True)
        raise AppDataError("the published file is not a gold export")
    os.replace(part, path)


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
