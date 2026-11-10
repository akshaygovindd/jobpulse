"""Print how many postings pass the role-family title filter (source of the figure in the docs).

    uv run python scripts/extraction_counts.py [--db path/to/local.duckdb]

Reads silver.int_extraction_candidates (built by `jobpulse build-silver` from the filter in
config/extraction.yaml). Prints counts only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jobpulse.config import load_extraction, load_pipeline
from jobpulse.extract.schema import prompt_version
from jobpulse.extract.store import Store
from jobpulse.secrets import load_env_keys
from jobpulse.warehouse import WarehouseError, connect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    args = parser.parse_args()
    pipeline, ecfg = load_pipeline(), load_extraction()
    if not args.db:
        load_env_keys([pipeline.warehouse.token_env_var])
    try:
        con = connect(pipeline.warehouse, db_path=Path(args.db) if args.db else None)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    store = Store(con, pipeline.warehouse, ecfg, prompt_version(ecfg.schema_version))
    active, cand = store.candidates_count()
    print(f"active postings: {active}")
    print(f"pass title filter: {cand} ({100 * cand / active:.1f}%)" if active else "no postings")
    for family, source, n in store.family_counts():
        print(f"  {family:<20} {source:<11} {n}")
    pending, hits = store.pending()
    print(f"already extracted (cache hits): {hits}; still to extract: {len(pending)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
