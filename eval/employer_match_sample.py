"""Write the BLIND employer-match sheet (about 60 pairs) for the owner to label yes/no.

    uv run python eval/employer_match_sample.py [--db local.duckdb] [--force]

Needs `jobpulse load-lca` to have run. The sheet shows only: pair id, company, the LCA employer
name, and its case count. Whether a pair is one of our matches or just a similar name, and the
method and confidence, are in `employer_match_manifest.json`, which the labeler must not open.
Copy `employer_match_labels_template.csv` to `employer_match_labels.csv`, fill the `label` column
with yes (same employer), no (different employer) or unsure, then run employer_match_score.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from employer_match import build_pairs, write_sheet

from jobpulse.cli import _warehouse
from jobpulse.config import load_employers, load_lca, load_pipeline
from jobpulse.warehouse import WarehouseError

HERE = Path(__file__).resolve().parent
SHEET = HERE / "employer_match_labels_template.csv"
MANIFEST = HERE / "employer_match_manifest.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    ap.add_argument("--force", action="store_true", help="overwrite an existing sheet")
    args = ap.parse_args(argv)
    if (SHEET.exists() or MANIFEST.exists()) and not args.force:
        print("error: a sheet already exists; pass --force to redraw it", file=sys.stderr)
        return 2
    if (HERE / "employer_match_labels.csv").exists() and args.force:
        print(
            "error: employer_match_labels.csv exists; move it away before redrawing",
            file=sys.stderr,
        )
        return 2
    lca = load_lca()
    wh = load_pipeline().warehouse
    try:
        con = _warehouse(argparse.Namespace(db=args.db, env="prod"))
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    n = con.execute(f'SELECT count(*) FROM "{wh.ref_schema}"."lca_employer_names"').fetchone()
    if not n or not n[0]:
        print(
            "error: ref.lca_employer_names is empty: run `jobpulse load-lca` first", file=sys.stderr
        )
        return 2
    names = sorted({e.company for e in load_employers().employers})
    pairs = build_pairs(con, wh, lca, names)
    write_sheet(pairs, SHEET, MANIFEST, lca.review.seed)
    n_matched = sum(1 for p in pairs if p.origin == "matched")
    print(f"wrote {len(pairs)} pairs ({n_matched} are our matches, the rest are near-misses)")
    if len(pairs) < 50:
        print(f"warning: only {len(pairs)} candidate pairs exist; the 50-pair target cannot be met")
    print(f"label {SHEET.name} (copy to employer_match_labels.csv): yes / no / unsure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
