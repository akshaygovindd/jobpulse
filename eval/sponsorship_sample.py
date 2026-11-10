"""Draw the targeted sponsorship sample (separate from the main 200) and write its BLIND sheet.

    uv run python eval/sponsorship_sample.py [--db local.duckdb] [--force]

Candidates are title-filtered postings whose TEXT mentions sponsor / visa / work authorization /
H-1B (pattern in config/extraction.yaml). Postings already in the main sample are excluded, so
nothing is labeled twice. Selection never looks at model output. Up to `sponsorship_sample.size`
rows; if fewer match, all remaining matches are taken.

`sponsorship_labels_template.csv` has only posting_key, title, company, description_text and ONE
empty label column, sponsorship_statement. Here a blank cell means "not labeled yet": write
offered, not_offered or unspecified explicitly. `score.py` uses this sample only in the
sponsorship report, never in the per-field report.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from common import (
    BLIND_COLUMNS,
    MANIFEST_PATH,
    SPONSOR_LABEL_COLUMNS,
    SPONSOR_MANIFEST_PATH,
    SPONSOR_TEMPLATE_PATH,
)

from jobpulse.config import load_extraction, load_pipeline
from jobpulse.secrets import load_env_keys
from jobpulse.warehouse import WarehouseError, connect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    ap.add_argument("--out", default=str(SPONSOR_TEMPLATE_PATH))
    ap.add_argument("--force", action="store_true", help="overwrite an existing sample")
    args = ap.parse_args()
    out = Path(args.out)
    if (out.exists() or SPONSOR_MANIFEST_PATH.exists()) and not args.force:
        print(f"error: {out.name} exists; refusing to redraw the sample (use --force)")
        return 2
    if not MANIFEST_PATH.exists():
        print("error: draw the main sample first (eval/sample.py)")
        return 2
    cfg, pipeline = load_extraction(), load_pipeline()
    spec = cfg.sponsorship_sample
    main_hashes = {
        i["content_hash"] for i in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["items"]
    }
    if not args.db:
        load_env_keys([pipeline.warehouse.token_env_var])
    try:
        con = connect(pipeline.warehouse, db_path=Path(args.db) if args.db else None)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rows = con.execute(
        f"""SELECT posting_key, content_hash, title, company, description_text
            FROM "{pipeline.warehouse.silver_schema}"."int_extraction_candidates"
            WHERE regexp_matches(description_text, ?, 'i')
            QUALIFY row_number() OVER (PARTITION BY content_hash ORDER BY posting_key) = 1
            ORDER BY posting_key""",
        [spec.pattern],
    ).fetchall()
    eligible = [r for r in rows if r[1] not in main_hashes]
    rng = random.Random(spec.seed)
    chosen = rng.sample(eligible, min(spec.size, len(eligible)))
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(BLIND_COLUMNS + SPONSOR_LABEL_COLUMNS)
        for posting_key, _hash, title, company, description in chosen:
            w.writerow(
                [posting_key, title, company, description] + [""] * len(SPONSOR_LABEL_COLUMNS)
            )
    manifest = {
        "seed": spec.seed,
        "pattern": spec.pattern,
        "requested": spec.size,
        "size": len(chosen),
        "created_at": datetime.now(UTC).isoformat(),
        "matching_candidates": len(rows),
        "already_in_main_sample": len(rows) - len(eligible),
        "items": [{"posting_key": c[0], "content_hash": c[1]} for c in chosen],
    }
    SPONSOR_MANIFEST_PATH.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(
        f"{len(rows)} candidates mention the terms; {len(rows) - len(eligible)} are in the main "
        f"sample; sampled {len(chosen)} (requested up to {spec.size})"
    )
    print(f"wrote {out} (blind) and {SPONSOR_MANIFEST_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
