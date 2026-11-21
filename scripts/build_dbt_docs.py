"""Generate the dbt docs site (lineage graph, model and test descriptions) into a directory.

Uses `--empty-catalog`, so no warehouse connection or token is needed: only the model
SQL, YAML and the same dbt variables the daily pipeline passes. Used by the dbt_docs workflow.

Usage: uv run python scripts/build_dbt_docs.py OUT_DIR
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from jobpulse.config import load_extraction, load_pipeline
from jobpulse.gold import gold_variables
from jobpulse.silver import DBT_DIR, silver_variables


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(args[0])
    from dbt.cli.main import dbtRunner

    pipeline = load_pipeline()
    ecfg = load_extraction()
    variables = {
        **silver_variables(pipeline.warehouse, ecfg),
        **gold_variables(pipeline, ecfg),
    }
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["JOBPULSE_DBT_PATH"] = str(Path(tmp) / "docs.duckdb")
        target = Path(tmp) / "target"
        res = dbtRunner().invoke(
            [
                "docs",
                "generate",
                "--empty-catalog",
                "--project-dir",
                str(DBT_DIR),
                "--profiles-dir",
                str(DBT_DIR),
                "--target",
                "ci",
                "--target-path",
                str(target),
                "--log-path",
                str(Path(tmp) / "logs"),
                "--vars",
                json.dumps(variables),
            ]
        )
        if not res.success:
            print(f"dbt docs generate failed: {res.exception}", file=sys.stderr)
            return 1
        out.mkdir(parents=True, exist_ok=True)
        for name in ("index.html", "manifest.json", "catalog.json"):
            shutil.copy(target / name, out / name)
    print(f"wrote dbt docs to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
