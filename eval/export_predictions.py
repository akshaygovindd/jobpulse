"""Write the model's predictions for the sampled postings to a SEPARATE file.

    uv run python eval/export_predictions.py [--db local.duckdb]

Do not open the output while labeling. `score.py` joins it with your labels by posting_key.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from common import (
    MANIFEST_PATH,
    PREDICTION_COLUMNS,
    PREDICTIONS_PATH,
    SPONSOR_MANIFEST_PATH,
    SPONSOR_PREDICTION_COLUMNS,
    SPONSOR_PREDICTIONS_PATH,
)

from jobpulse.config import load_extraction, load_pipeline
from jobpulse.extract.schema import prompt_version
from jobpulse.secrets import load_env_keys
from jobpulse.warehouse import WarehouseError, connect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    ap.add_argument("--out", default=str(PREDICTIONS_PATH))
    args = ap.parse_args()
    ecfg = load_extraction()
    pipeline = load_pipeline()
    if not args.db:
        load_env_keys([pipeline.warehouse.token_env_var])
    try:
        con = connect(pipeline.warehouse, db_path=Path(args.db) if args.db else None)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    s = ecfg.extract_schema
    params = [ecfg.schema_version, prompt_version(ecfg.schema_version)]
    out_rows: list[list[object]] = []
    counts = {"extracted": 0, "quarantined": 0, "missing": 0}
    for item in manifest["items"]:
        h = item["content_hash"]
        e = con.execute(
            f"""SELECT model, escalated, seniority, role_family, remote_policy, salary_min,
                       salary_max, salary_currency, salary_period, sponsorship_statement,
                       sponsorship_original_text, skills
                FROM "{s}"."extractions"
                WHERE content_hash = ? AND schema_version = ? AND prompt_version = ?""",
            [h, *params],
        ).fetchone()
        if e is not None:
            counts["extracted"] += 1
            skills = ";".join(e[11] or [])
            out_rows.append([item["posting_key"], h, "extracted", *e[:11], skills])
            continue
        q = con.execute(
            f'SELECT 1 FROM "{s}"."quarantine" WHERE content_hash = ? '
            "AND schema_version = ? AND prompt_version = ?",
            [h, *params],
        ).fetchone()
        status = "quarantined" if q else "missing"
        counts[status] += 1
        out_rows.append([item["posting_key"], h, status] + [""] * (len(PREDICTION_COLUMNS) - 3))
    with Path(args.out).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(PREDICTION_COLUMNS)
        w.writerows(out_rows)
    print(f"wrote {args.out}: {counts}")

    if SPONSOR_MANIFEST_PATH.exists():  # targeted sponsorship sample: separate predictions file
        sm = json.loads(SPONSOR_MANIFEST_PATH.read_text(encoding="utf-8"))
        sp_rows: list[list[object]] = []
        sp_counts = {"extracted": 0, "quarantined": 0, "missing": 0}
        for item in sm["items"]:
            h = item["content_hash"]
            e = con.execute(
                f"""SELECT model, sponsorship_statement, sponsorship_original_text
                    FROM "{s}"."extractions"
                    WHERE content_hash = ? AND schema_version = ? AND prompt_version = ?""",
                [h, *params],
            ).fetchone()
            if e is not None:
                sp_counts["extracted"] += 1
                sp_rows.append([item["posting_key"], h, "extracted", *e])
                continue
            q = con.execute(
                f'SELECT 1 FROM "{s}"."quarantine" WHERE content_hash = ? '
                "AND schema_version = ? AND prompt_version = ?",
                [h, *params],
            ).fetchone()
            status = "quarantined" if q else "missing"
            sp_counts[status] += 1
            sp_rows.append([item["posting_key"], h, status, "", "", ""])
        with SPONSOR_PREDICTIONS_PATH.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SPONSOR_PREDICTION_COLUMNS)
            w.writerows(sp_rows)
        print(f"wrote {SPONSOR_PREDICTIONS_PATH}: {sp_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
