"""Shared helpers for the automated (no human labels) evaluations."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from jobpulse.config import ExtractionConfig, PipelineConfig, load_extraction, load_pipeline
from jobpulse.extract.schema import prompt_version
from jobpulse.secrets import load_env_keys
from jobpulse.warehouse import WarehouseError, connect

EXTRACTION_FIELDS = [
    "seniority",
    "role_family",
    "remote_policy",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "sponsorship_statement",
    "sponsorship_original_text",
    "salary_original_text",
    "skills",
]


@dataclass
class Ctx:
    con: duckdb.DuckDBPyConnection
    pipeline: PipelineConfig
    ecfg: ExtractionConfig
    prompt_version: str


def open_ctx(db: str | None) -> Ctx | None:
    pipeline, ecfg = load_pipeline(), load_extraction()
    if not db:
        load_env_keys([pipeline.warehouse.token_env_var])
    try:
        con = connect(pipeline.warehouse, db_path=Path(db) if db else None)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return None
    return Ctx(con, pipeline, ecfg, prompt_version(ecfg.schema_version))


def fetch_extractions(ctx: Ctx) -> list[dict[str, Any]]:
    """Every stored extraction for the current schema/prompt version, with the posting text when
    it is still in silver (text is None for postings that have since left the data)."""
    s, silver = ctx.ecfg.extract_schema, ctx.pipeline.warehouse.silver_schema
    cols = ", ".join(f"e.{c}" for c in EXTRACTION_FIELDS)
    cur = ctx.con.execute(
        f"""SELECT e.content_hash, e.posting_key, e.model, e.escalated, {cols},
                   d.title, d.company, d.description_text
            FROM "{s}"."extractions" e
            LEFT JOIN (
                SELECT content_hash, title, company, description_text
                FROM "{silver}"."dim_postings" WHERE source_status = 'active'
                QUALIFY row_number() OVER (PARTITION BY content_hash ORDER BY posting_key) = 1
            ) d ON d.content_hash = e.content_hash
            WHERE e.schema_version = ? AND e.prompt_version = ?
            ORDER BY e.content_hash""",
        [ctx.ecfg.schema_version, ctx.prompt_version],
    )
    names = [c[0] for c in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def pct(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{100 * num / den:.1f}% ({num}/{den})"
