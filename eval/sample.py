"""Draw the stratified benchmark sample and write the BLIND labeling sheet.

    uv run python eval/sample.py [--db local.duckdb] [--force]

The sample is drawn from the title-filtered candidate set (silver.int_extraction_candidates),
stratified by (title_family, source). Selection never looks at model output.

`labels_template.csv` holds ONLY posting_key, title, company, description_text and empty label
columns: no predictions, no title_family, no model fields, so labeling is blind. Predictions live
in a separate file (`export_predictions.py`) that `score.py` joins by posting_key.
`sample_manifest.json` records the seed, strata sizes and each posting's content_hash.

Row order is a seeded, prefix-balanced interleave: any first N rows contain each stratum in
(nearly) its full-sample proportion, so you can score after 100 rows and still have a
representative subset. `--reorder` re-applies the order to an existing sheet (same postings, no
redraw); it refuses to run once any label cell is filled.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from common import BLIND_COLUMNS, LABEL_COLUMNS, MANIFEST_PATH, TEMPLATE_PATH

from jobpulse.config import ExtractionConfig, PipelineConfig, load_extraction, load_pipeline
from jobpulse.secrets import load_env_keys
from jobpulse.warehouse import WarehouseError, connect

Stratum = tuple[str, str]


def allocate(sizes: dict[Stratum, int], total: int) -> dict[Stratum, int]:
    """Proportional allocation (largest remainder), at least 1 per non-empty stratum when
    possible, never more than the stratum holds. Returns exactly min(total, population)."""
    population = sum(sizes.values())
    target = min(total, population)
    keys = sorted(sizes)
    alloc = {k: 0 for k in keys}
    if target >= len([k for k in keys if sizes[k] > 0]):
        for k in keys:
            alloc[k] = 1 if sizes[k] > 0 else 0
    remaining = target - sum(alloc.values())
    quota = {k: sizes[k] * target / population for k in keys}
    # Fill toward each stratum's proportional quota, largest shortfall first.
    while remaining > 0:
        open_keys = [k for k in keys if alloc[k] < sizes[k]]
        best = max(open_keys, key=lambda k: (quota[k] - alloc[k], sizes[k], k))
        alloc[best] += 1
        remaining -= 1
    return alloc


def balanced_order[T](groups: dict[Stratum, list[T]], rng: random.Random) -> list[T]:
    """Interleave strata so every prefix is proportional: within a stratum the items are shuffled,
    and the j-th of n items gets the key (j + u) / n for a per-stratum random offset u in [0, 1)."""
    keyed: list[tuple[float, Stratum, int, T]] = []
    for key in sorted(groups):
        items = list(groups[key])
        rng.shuffle(items)
        offset = rng.random()
        n = len(items)
        keyed.extend(((j + offset) / n, key, j, item) for j, item in enumerate(items))
    keyed.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in keyed]


def reorder_existing(
    args: argparse.Namespace, cfg: ExtractionConfig, pipeline: PipelineConfig
) -> int:
    """Re-order the existing blind sheet and manifest (same postings) with the balanced order."""
    out = Path(args.out)
    with out.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    if any(r[c] for r in rows for c in LABEL_COLUMNS):
        print("error: labels already entered; refusing to reorder")
        return 2
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not args.db:
        load_env_keys([pipeline.warehouse.token_env_var])
    try:
        con = connect(pipeline.warehouse, db_path=Path(args.db) if args.db else None)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    strata_of = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            f"SELECT posting_key, title_family, source_id "
            f'FROM "{pipeline.warehouse.silver_schema}"."int_extraction_candidates"'
        ).fetchall()
    }
    groups: dict[Stratum, list[dict[str, str]]] = {}
    for r in sorted(rows, key=lambda r: r["posting_key"]):
        groups.setdefault(strata_of[r["posting_key"]], []).append(r)
    ordered = balanced_order(groups, random.Random(cfg.sample.seed))
    assert {r["posting_key"] for r in ordered} == {r["posting_key"] for r in rows}
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(ordered)
    by_key = {i["posting_key"]: i for i in manifest["items"]}
    manifest["items"] = [by_key[r["posting_key"]] for r in ordered]
    manifest["order"] = "prefix_balanced"
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"reordered {len(ordered)} rows (same postings, prefix-balanced, seed {cfg.sample.seed})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    ap.add_argument("--out", default=str(TEMPLATE_PATH))
    ap.add_argument("--force", action="store_true", help="overwrite an existing sample")
    ap.add_argument("--reorder", action="store_true", help="reorder the existing sheet only")
    args = ap.parse_args()
    out = Path(args.out)
    if args.reorder:
        return reorder_existing(args, load_extraction(), load_pipeline())
    if (out.exists() or MANIFEST_PATH.exists()) and not args.force:
        print(f"error: {out.name} exists; refusing to redraw the sample (use --force)")
        return 2
    cfg = load_extraction()
    pipeline = load_pipeline()
    if not args.db:
        load_env_keys([pipeline.warehouse.token_env_var])
    try:
        con = connect(pipeline.warehouse, db_path=Path(args.db) if args.db else None)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rows = con.execute(
        f"""SELECT title_family, source_id, posting_key, content_hash, title, company,
                   description_text
            FROM "{pipeline.warehouse.silver_schema}"."int_extraction_candidates"
            QUALIFY row_number() OVER (PARTITION BY content_hash ORDER BY posting_key) = 1
            ORDER BY posting_key"""
    ).fetchall()
    strata: dict[Stratum, list[tuple[str, ...]]] = {}
    for r in rows:
        strata.setdefault((r[0], r[1]), []).append(tuple(r[2:]))
    alloc = allocate({k: len(v) for k, v in strata.items()}, cfg.sample.size)
    rng = random.Random(cfg.sample.seed)
    picked = {key: rng.sample(strata[key], alloc[key]) for key in sorted(strata)}
    chosen = balanced_order(
        picked, rng
    )  # order must not reveal the stratum, prefixes stay proportional
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(BLIND_COLUMNS + LABEL_COLUMNS)
        for posting_key, _hash, title, company, description in chosen:
            w.writerow([posting_key, title, company, description] + [""] * len(LABEL_COLUMNS))
    manifest = {
        "seed": cfg.sample.seed,
        "size": len(chosen),
        "created_at": datetime.now(UTC).isoformat(),
        "order": "prefix_balanced",
        "population": len(rows),
        "strata": {f"{k[0]}|{k[1]}": {"population": len(strata[k]), "n": alloc[k]} for k in strata},
        "items": [{"posting_key": c[0], "content_hash": c[1]} for c in chosen],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"sampled {len(chosen)} of {len(rows)} candidates across {len(strata)} strata")
    print(f"wrote {out} (blind: no predictions) and {MANIFEST_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
