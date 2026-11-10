"""Warehouse connection (MotherDuck in production, a local DuckDB file in tests).

The MotherDuck token is read from the environment by DuckDB itself. It is never put in a
connection string, logged, or echoed; error text is scrubbed before it can surface.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

from jobpulse.config import WarehouseConfig


class WarehouseError(Exception):
    pass


def _scrub(message: str, token: str | None) -> str:
    return message.replace(token, "<redacted>") if token else message


def connect(cfg: WarehouseConfig, *, db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open the warehouse and make sure the ops/bronze schemas exist.

    `db_path` selects a local DuckDB file (tests and dev); otherwise MotherDuck is used and the
    database is created on first use.
    """
    if db_path is not None:
        con = duckdb.connect(str(db_path))
    else:
        token = os.environ.get(cfg.token_env_var)
        if not token:
            raise WarehouseError(f"set {cfg.token_env_var} (or pass --db for a local file)")
        try:
            con = duckdb.connect("md:")
            con.execute(f'CREATE DATABASE IF NOT EXISTS "{cfg.database}"')
            con.execute(f'USE "{cfg.database}"')
        except duckdb.Error as e:
            raise WarehouseError(f"cannot connect to MotherDuck: {_scrub(str(e), token)}") from None
    for schema in (cfg.ops_schema, cfg.bronze_schema, cfg.silver_schema):
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    return con
