"""Build the silver layer by running the dbt project (models + tests) against the warehouse.

Tunables come from config/pipeline.yaml and are passed to dbt as vars, so SQL has no magic
numbers. `db_path` selects the local `ci` target; otherwise the MotherDuck `prod` target.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from jobpulse.config import REPO_ROOT, ExtractionConfig, WarehouseConfig, load_extraction

DBT_DIR = REPO_ROOT / "dbt"


@dataclass
class SilverResult:
    success: bool
    models_built: int = 0
    tests_passed: int = 0
    failures: list[str] = field(default_factory=list)
    error: str | None = None


def run_dbt(
    cfg: WarehouseConfig,
    select: list[str],
    variables: dict[str, object],
    *,
    db_path: Path | None = None,
) -> SilverResult:
    """Run `dbt build` (models + tests + snapshots) for the selected paths. A DuckDB file selects
    the local `ci` target; otherwise the MotherDuck `prod` target."""
    from dbt.cli.main import dbtRunner  # lazy: ingest-only installs don't have the dbt group

    if db_path is not None:
        os.environ["JOBPULSE_DBT_PATH"] = str(db_path)
        target = "ci"
    else:
        os.environ["JOBPULSE_DBT_DB"] = cfg.database
        target = "prod"
    with tempfile.TemporaryDirectory() as tmp:
        res = dbtRunner().invoke(
            [
                "build",
                "--project-dir",
                str(DBT_DIR),
                "--profiles-dir",
                str(DBT_DIR),
                "--target",
                target,
                "--select",
                *select,
                "--target-path",
                str(Path(tmp) / "target"),
                "--log-path",
                str(Path(tmp) / "logs"),
                "--vars",
                json.dumps(variables),
            ]
        )
    result = SilverResult(success=res.success)
    if res.exception is not None:
        result.error = type(res.exception).__name__
    for node in getattr(res.result, "results", None) or []:
        status = str(node.status)
        name = node.node.name
        is_test = node.node.resource_type.value == "test"
        if status in {"success", "pass"}:
            if is_test:
                result.tests_passed += 1
            else:
                result.models_built += 1
        else:
            result.failures.append(f"{name}: {status}")
    return result


def build_silver(
    cfg: WarehouseConfig,
    *,
    db_path: Path | None = None,
    extraction: ExtractionConfig | None = None,
) -> SilverResult:
    flt = (extraction or load_extraction()).title_filter
    variables: dict[str, object] = {
        "extraction_exclude": flt.exclude,
        "extraction_families": [f.model_dump() for f in flt.families],
        "bronze_schema": cfg.bronze_schema,
        "silver_schema": cfg.silver_schema,
        # dbt renders project-level config with CLI vars only, so every schema is always passed.
        "gold_schema": cfg.gold_schema,
        "gold_stage_schema": cfg.gold_stage_schema,
        "closure_consecutive_absences": cfg.closure_consecutive_absences,
    }
    return run_dbt(cfg, ["path:models/staging", "path:models/silver"], variables, db_path=db_path)
