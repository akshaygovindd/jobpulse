"""Report extraction cost and quality-of-operation metrics from the llm_calls log.

    uv run python scripts/report_extraction_cost.py [--db path/to/local.duckdb]

Prints cost per 1k postings, escalation rate and quarantine rate. Evaluation-only calls
(mode='eval', from eval/agreement.py) are excluded from those figures and shown separately.
Every figure comes from extract.llm_calls / extractions / quarantine; nothing is estimated.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jobpulse.config import load_extraction, load_pipeline
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
    s = ecfg.extract_schema

    def one(sql: str) -> tuple[float, ...]:
        row = con.execute(sql).fetchone()
        return tuple(float(x or 0) for x in (row or ()))

    try:
        ext, esc = one(
            f'SELECT count(*), count(*) FILTER (WHERE escalated) FROM "{s}"."extractions"'
        )
        (quar,) = one(f'SELECT count(*) FROM "{s}"."quarantine"')
        calls, cost, tin, tout = one(
            f"SELECT count(*), sum(cost_usd), sum(input_tokens), sum(output_tokens) "
            f'FROM "{s}"."llm_calls" WHERE mode <> \'eval\''
        )
        eval_calls, eval_cost = one(
            f'SELECT count(*), sum(cost_usd) FROM "{s}"."llm_calls" WHERE mode = \'eval\''
        )
    except Exception:
        print("no extraction tables yet (run `jobpulse extract` first)")
        return 0
    done = ext + quar
    print(f"postings processed: {done:.0f} (extracted {ext:.0f}, quarantined {quar:.0f})")
    print(f"llm calls: {calls:.0f}; tokens in/out: {tin:.0f}/{tout:.0f}; total cost: ${cost:.4f}")
    if eval_calls:
        print(
            f"(excluded: {eval_calls:.0f} evaluation-only calls costing ${eval_cost:.4f}, "
            "from eval/agreement.py)"
        )
    if done:
        print(f"cost per 1k postings: ${1000 * cost / done:.2f}")
        print(f"escalation rate: {100 * (esc + quar) / done:.1f}%")
        print(f"quarantine rate: {100 * quar / done:.1f}%")
    for mode, model, n, c in con.execute(
        f"SELECT mode, model, count(*), sum(cost_usd) "
        f'FROM "{s}"."llm_calls" GROUP BY ALL ORDER BY 1, 2'
    ).fetchall():
        print(f"  {mode:<6} {model:<16} calls={n:<5} cost=${c:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
