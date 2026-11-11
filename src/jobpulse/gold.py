"""Build the gold layer: stage the Python-computed mapping tables, then run the gold dbt models,
tests and the postings snapshot.

Gold reads silver and `extract.extractions` and never writes either. It is safe to run when
extraction has not run (or failed): postings without an extraction simply have has_extraction =
false. Mappings (canonical skill names, city) are computed in Python so the eval scorer and gold
share one normalization; they are rebuilt from scratch each run, so reruns are idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from jobpulse.config import (
    ExtractionConfig,
    LocationsConfig,
    PipelineConfig,
    load_extraction,
    load_locations,
    load_pipeline,
)
from jobpulse.extract.store import ensure_extract_tables
from jobpulse.location import CityParser
from jobpulse.silver import SilverResult, run_dbt
from jobpulse.skills import norm_skill
from jobpulse.warehouse import connect


@dataclass
class StagedMaps:
    skills: int
    locations: int


def stage_maps(
    con: duckdb.DuckDBPyConnection,
    pipeline: PipelineConfig,
    ecfg: ExtractionConfig,
    locations: LocationsConfig,
) -> StagedMaps:
    """(Re)create gold_stage.skill_map and gold_stage.location_map from the current data."""
    wh = pipeline.warehouse
    stage = wh.gold_stage_schema
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{wh.gold_schema}"')
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{stage}"')
    ensure_extract_tables(con, ecfg)

    raw_skills = [
        str(r[0])
        for r in con.execute(
            f'SELECT DISTINCT unnest(skills) FROM "{ecfg.extract_schema}"."extractions"'
        ).fetchall()
        if r[0] is not None
    ]
    skill_rows = [(raw, norm_skill(raw)) for raw in sorted(raw_skills)]
    con.execute(f'DROP TABLE IF EXISTS "{stage}"."skill_map"')
    con.execute(f'CREATE TABLE "{stage}"."skill_map" (raw_skill VARCHAR, canonical_name VARCHAR)')
    if skill_rows:
        con.executemany(f'INSERT INTO "{stage}"."skill_map" VALUES (?, ?)', skill_rows)

    parser = CityParser(locations)
    raw_locs = [
        str(r[0])
        for r in con.execute(
            f'SELECT DISTINCT location_raw FROM "{wh.silver_schema}"."dim_postings" '
            "WHERE location_raw IS NOT NULL"
        ).fetchall()
    ]
    loc_rows = [(raw, parser.parse(raw)) for raw in sorted(raw_locs)]
    con.execute(f'DROP TABLE IF EXISTS "{stage}"."location_map"')
    con.execute(f'CREATE TABLE "{stage}"."location_map" (location_raw VARCHAR, city VARCHAR)')
    if loc_rows:
        con.executemany(f'INSERT INTO "{stage}"."location_map" VALUES (?, ?)', loc_rows)
    return StagedMaps(skills=len(skill_rows), locations=len(loc_rows))


def build_gold(
    pipeline: PipelineConfig | None = None,
    *,
    db_path: Path | None = None,
    extraction: ExtractionConfig | None = None,
    locations: LocationsConfig | None = None,
) -> tuple[SilverResult, StagedMaps]:
    pipeline = pipeline or load_pipeline()
    ecfg = extraction or load_extraction()
    wh = pipeline.warehouse
    con = connect(wh, db_path=db_path)
    try:
        maps = stage_maps(con, pipeline, ecfg, locations or load_locations())
    finally:
        con.close()  # release the file lock before dbt opens its own connection
    variables: dict[str, object] = {
        "bronze_schema": wh.bronze_schema,
        "silver_schema": wh.silver_schema,
        "gold_schema": wh.gold_schema,
        "gold_stage_schema": wh.gold_stage_schema,
        "extract_schema": ecfg.extract_schema,
        "annualization": pipeline.gold.annualization,
        "min_salaries_for_percentiles": pipeline.gold.min_salaries_for_percentiles,
    }
    result = run_dbt(wh, ["path:models/gold", "path:snapshots"], variables, db_path=db_path)
    return result, maps
