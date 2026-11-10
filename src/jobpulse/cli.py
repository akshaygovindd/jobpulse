"""`jobpulse` CLI. The same entrypoints are used by GitHub Actions and (later) Airflow."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from jobpulse.bronze.loader import BronzeResult, load_bronze
from jobpulse.config import (
    CONFIG_DIR,
    load_employers,
    load_extraction,
    load_pipeline,
    load_sources,
)
from jobpulse.connectors import build_connectors
from jobpulse.extract.cost import projected_cost
from jobpulse.extract.llm import OpenAIClient
from jobpulse.extract.runner import (
    ExtractionError,
    RunStats,
    collect_batch,
    run_extraction,
    submit_batch,
)
from jobpulse.extract.schema import prompt_version
from jobpulse.extract.store import Store
from jobpulse.freshness import check_freshness
from jobpulse.freshness import render_markdown as render_freshness
from jobpulse.http import HttpClient
from jobpulse.ingest.identity import (
    Clock,
    SnapshotDateError,
    detect_env,
    make_attempt_id,
    resolve_snapshot_date,
    utc_now,
)
from jobpulse.ingest.report import build_report, render_markdown
from jobpulse.ingest.runner import run_ingest
from jobpulse.ingest.storage import LocalStorage, S3Storage, Storage
from jobpulse.logging import configure_logging, get_logger
from jobpulse.ops.load_ops import load_ops, record_pipeline_run
from jobpulse.ops.s3_ops import write_ops
from jobpulse.secrets import load_env_keys
from jobpulse.silver import build_silver
from jobpulse.validate import dump_employers_yaml, refreshed, validate_employers
from jobpulse.warehouse import WarehouseError
from jobpulse.warehouse import connect as connect_warehouse

EXIT_OK = 0
EXIT_ALERT = 1  # run finished, but results need attention (low success ratio, stale data)
EXIT_USAGE = 2  # rejected before doing any work (bad args, bad snapshot_date, no bucket)


def _storage(args: argparse.Namespace) -> Storage:
    if args.local_dir:
        return LocalStorage(Path(args.local_dir))
    var = load_pipeline().storage.bucket_env_var
    bucket = os.environ.get(var)
    if not bucket:
        raise SystemExit(f"error: set {var} or pass --local-dir")
    return S3Storage(bucket)


def _append_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")


def cmd_ingest(args: argparse.Namespace, clock: Clock) -> int:
    log = get_logger(command="ingest")
    now = clock()
    try:
        snapshot_date = resolve_snapshot_date(args.snapshot_date, now)
    except SnapshotDateError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    pipeline = load_pipeline()
    employers = load_employers().employers
    if args.sources:
        wanted = set(args.sources.split(","))
        employers = [e for e in employers if e.source in wanted]
    if args.board:
        employers = [e for e in employers if e.board_id in set(args.board)]
    storage = _storage(args)
    attempt_id = make_attempt_id(now)

    with HttpClient(pipeline.http) as http:
        run = run_ingest(
            employers,
            build_connectors(http, pipeline.ingest),
            storage,
            snapshot_date=snapshot_date,
            attempt_id=attempt_id,
            env=detect_env(),
            clock=clock,
            max_workers=pipeline.ingest.max_workers,
        )
    report = build_report(run)
    keys = write_ops(storage, run, load_sources(), report)
    markdown = render_markdown(report)
    print(markdown)
    _append_step_summary(markdown)
    log.info("ops_written", **keys)
    if run.success_ratio < pipeline.ingest.min_success_ratio:
        log.error("success_ratio_below_threshold", ratio=run.success_ratio)
        return EXIT_ALERT
    return EXIT_OK


def _warehouse(args: argparse.Namespace) -> duckdb.DuckDBPyConnection:
    path = Path(args.db) if args.db else None
    cfg = load_pipeline().warehouse
    if path is None:
        load_env_keys([cfg.token_env_var])  # from .env inside Python; never sourced in a shell
    return connect_warehouse(cfg, db_path=path)


def cmd_load_ops(args: argparse.Namespace, clock: Clock) -> int:
    cfg = load_pipeline().warehouse
    try:
        con = _warehouse(args)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    result = load_ops(_storage(args), con, cfg)
    get_logger(command="load-ops").info(
        "ops_loaded",
        attempts=result.attempts_loaded,
        pipeline_runs=result.pipeline_runs_loaded,
        company_snapshots=result.company_snapshots_loaded,
        registry_rows=result.registry_rows,
    )
    return EXIT_OK


def _bronze_summary(result: BronzeResult) -> str:
    lines = [
        "## Bronze load",
        "",
        f"- Pointers seen: **{result.pointers_seen}**",
        f"- Loaded: **{result.loaded}** ({result.rows_loaded} postings)",
        f"- Unchanged (same attempt already loaded): **{result.skipped_unchanged}**",
        f"- Failed: **{len(result.failed)}**",
    ]
    if result.failed:
        lines += ["", "| Pointer | Reason |", "|---|---|"]
        lines += [f"| `{f.pointer_key}` | `{f.reason}` |" for f in result.failed]
    return "\n".join(lines) + "\n"


def cmd_load_bronze(args: argparse.Namespace, clock: Clock) -> int:
    cfg = load_pipeline().warehouse
    try:
        con = _warehouse(args)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    now = clock()
    attempt_id = make_attempt_id(now)
    result = load_bronze(_storage(args), con, cfg, clock=clock, attempt_id=attempt_id)
    summary = _bronze_summary(result)
    print(summary)
    _append_step_summary(summary)
    finished = clock()
    status = "success" if not result.failed else ("partial" if result.loaded else "failed")
    for source_id in sorted(result.rows_by_source) or ["all"]:
        record_pipeline_run(
            con,
            cfg,
            {
                "attempt_id": attempt_id,
                "snapshot_date": now.date().isoformat(),
                "task": "bronze",
                "source_id": source_id,
                "started_at": now.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_s": round((finished - now).total_seconds(), 3),
                "status": status,
                "rows_in": result.rows_by_source.get(source_id, 0),
                "rows_kept": result.rows_by_source.get(source_id, 0),
                "rows_dropped_json": {f.reason: 1 for f in result.failed},
                "llm_cost_usd": 0.0,
                "env": detect_env(),
            },
        )
    return EXIT_ALERT if result.failed else EXIT_OK


def cmd_build_silver(args: argparse.Namespace, clock: Clock) -> int:
    cfg = load_pipeline().warehouse
    try:
        con = _warehouse(args)  # also ensures the schemas exist
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    now = clock()
    attempt_id = make_attempt_id(now)
    con.close()  # release the file lock before dbt opens its own connection
    result = build_silver(cfg, db_path=Path(args.db) if args.db else None)
    finished = clock()
    con = _warehouse(args)
    postings = con.execute(f'SELECT count(*) FROM "{cfg.silver_schema}"."dim_postings"').fetchone()
    n_postings = int(postings[0]) if postings else 0
    record_pipeline_run(
        con,
        cfg,
        {
            "attempt_id": attempt_id,
            "snapshot_date": now.date().isoformat(),
            "task": "silver",
            "source_id": "all",
            "started_at": now.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_s": round((finished - now).total_seconds(), 3),
            "status": "success" if result.success else "failed",
            "rows_in": None,
            "rows_kept": n_postings,
            "rows_dropped_json": {f: 1 for f in result.failures},
            "llm_cost_usd": 0.0,
            "env": detect_env(),
        },
    )
    summary = (
        "## Silver build (dbt)\n\n"
        f"- Models built: **{result.models_built}**\n"
        f"- Tests passed: **{result.tests_passed}**\n"
        f"- Failures: **{len(result.failures)}**\n"
        + "".join(f"  - `{f}`\n" for f in result.failures)
        + (f"- Error: `{result.error}`\n" if result.error else "")
    )
    print(summary)
    _append_step_summary(summary)
    return EXIT_OK if result.success else EXIT_ALERT


def _extract_run_row(
    attempt_id: str, now: datetime, finished: datetime, task: str, **kw: Any
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "snapshot_date": now.date().isoformat(),
        "task": task,
        "source_id": "all",
        "started_at": now.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_s": round((finished - now).total_seconds(), 3),
        "status": kw.get("status", "success"),
        "rows_in": kw.get("rows_in"),
        "rows_kept": kw.get("rows_kept"),
        "rows_dropped_json": kw.get("dropped", {}),
        "llm_cost_usd": kw.get("cost", 0.0),
        "env": detect_env(),
    }


def _extract_summary(title: str, st: RunStats) -> str:
    return (
        f"## {title}\n\n"
        f"- Active postings: **{st.active}**; pass the title filter: **{st.candidates}**\n"
        f"- Cache hits: **{st.cache_hits}**; processed this run: **{st.pending}**\n"
        f"- Extracted: **{st.extracted}** (escalated {st.escalated}); quarantined: "
        f"**{st.quarantined}**; queued (budget): **{st.queued}**; errors: **{st.errors}**\n"
        f"- LLM calls: **{st.llm_calls}**; cost: **${st.cost_usd:.4f}** of cap ${st.cap_usd:.2f}"
        + (" (cap reached)" if st.cap_hit else "")
        + "\n"
    )


def cmd_extract(args: argparse.Namespace, clock: Clock) -> int:
    pipeline = load_pipeline()
    ecfg = load_extraction()
    try:
        con = _warehouse(args)
    except WarehouseError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    store = Store(con, pipeline.warehouse, ecfg, prompt_version(ecfg.schema_version))
    now = clock()
    attempt_id = make_attempt_id(now)
    if args.cap is not None:
        cap = args.cap
    else:
        cap = (
            ecfg.budget.backfill_cap_usd
            if (args.backfill or args.batch)
            else ecfg.budget.run_cap_usd
        )

    if args.dry_run:  # no API key needed, no network call to the provider
        active, cand = store.candidates_count()
        pending, hits = store.pending(args.limit)
        chars = [len(p.title) + len(p.description_text) for p in pending]
        avg = sum(chars) / len(chars) if chars else 0.0
        sync = projected_cost(len(pending), avg, ecfg, batch=False)
        bat = projected_cost(len(pending), avg, ecfg, batch=True)
        print(
            f"active postings: {active}\npass title filter: {cand}\ncache hits: {hits}\n"
            f"to extract: {len(pending)} (avg {avg:.0f} chars)\n"
            f"projected cost, sync:  ${sync['total']:.4f}\n"
            f"projected cost, batch: ${bat['total']:.4f} "
            f"(first pass ${bat['first_pass']:.4f} + escalation ${bat['escalation']:.4f})\n"
            f"spend cap for this run: ${cap:.2f}"
        )
        return EXIT_OK

    load_env_keys([ecfg.api_key_env_var])
    key = os.environ.get(ecfg.api_key_env_var)
    if not key:
        print(f"error: set {ecfg.api_key_env_var} (env or .env)", file=sys.stderr)
        return EXIT_USAGE
    log = get_logger(command="extract", attempt_id=attempt_id)
    with HttpClient(pipeline.http) as http:
        client = OpenAIClient(ecfg, http, key)
        try:
            if args.batch == "submit":
                info = submit_batch(
                    store,
                    ecfg,
                    client,
                    attempt_id=attempt_id,
                    cap_usd=cap,
                    clock=clock,
                    limit=args.limit,
                )
                print(f"batch submit: {info}")
                log.info("batch_submitted", **info)
                return EXIT_OK
            if args.batch == "collect":
                stats, notes = collect_batch(
                    store, ecfg, client, attempt_id=attempt_id, cap_usd=cap, clock=clock
                )
                print("\n".join(notes) or "no open batches")
            else:
                stats = run_extraction(
                    store,
                    ecfg,
                    client,
                    attempt_id=attempt_id,
                    cap_usd=cap,
                    clock=clock,
                    limit=args.limit,
                )
        except ExtractionError as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_USAGE
    finished = clock()
    wcfg = pipeline.warehouse
    record_pipeline_run(
        con,
        wcfg,
        _extract_run_row(
            attempt_id,
            now,
            finished,
            "extract_filter",
            rows_in=stats.active,
            rows_kept=stats.candidates,
            dropped={"title_filter": stats.active - stats.candidates},
        ),
    )
    record_pipeline_run(
        con,
        wcfg,
        _extract_run_row(
            attempt_id,
            now,
            finished,
            "extract",
            rows_in=stats.pending,
            rows_kept=stats.extracted,
            dropped={
                "quarantined": stats.quarantined,
                "queued": stats.queued,
                "errors": stats.errors,
                "cache_hits": stats.cache_hits,
            },
            cost=stats.cost_usd,
            status="success" if not stats.errors else "partial",
        ),
    )
    summary = _extract_summary("Extraction", stats)
    print(summary)
    _append_step_summary(summary)
    return EXIT_OK


def cmd_check_freshness(args: argparse.Namespace, clock: Clock) -> int:
    pipeline = load_pipeline()
    sources = load_sources().by_id()
    employers = [e for e in load_employers().employers if sources[e.source].enabled]
    results = check_freshness(
        _storage(args),
        employers,
        now=clock(),
        max_age_hours=pipeline.freshness.max_age_hours,
        lookback_days=pipeline.freshness.lookback_days,
    )
    markdown = render_freshness(results, pipeline.freshness.max_age_hours)
    print(markdown)
    _append_step_summary(markdown)
    stale = [r.source_id for r in results if r.stale]
    if stale:
        get_logger(command="check-freshness").error("stale_sources", sources=stale)
        return EXIT_ALERT
    return EXIT_OK


def cmd_validate_employers(args: argparse.Namespace, clock: Clock) -> int:
    pipeline = load_pipeline()
    employers = load_employers()
    cfg = pipeline.ingest.model_copy(update={"greenhouse_include_content": False})
    with HttpClient(pipeline.http) as http:
        results = validate_employers(employers, build_connectors(http, cfg))
    for r in results:
        status = f"ok  {r.open_job_count:>5}" if r.ok else f"ERR {r.reason_code}: {r.detail}"
        print(
            f"{r.employer.source:<10} {r.employer.board_id:<16} {r.employer.company:<14} {status}"
        )
    bad = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} boards valid")
    if args.write:
        path = CONFIG_DIR / "employers.yaml"
        path.write_text(dump_employers_yaml(refreshed(employers, results, clock())))
        print(f"updated {path}")
    return EXIT_ALERT if bad else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobpulse")
    sub = p.add_subparsers(dest="command", required=True)

    ing = sub.add_parser("ingest", help="fetch all enabled employer boards for today (UTC)")
    ing.add_argument(
        "--snapshot-date",
        help="must equal the current UTC date (YYYY-MM-DD); defaults to today. "
        "Past and future dates are rejected: missed days stay gaps.",
    )
    ing.add_argument("--sources", help="comma-separated source ids (default: all P1 sources)")
    ing.add_argument("--board", action="append", help="limit to board id(s); repeatable")
    ing.add_argument("--local-dir", help="write to a local directory instead of S3")

    lo = sub.add_parser("load-ops", help="load S3 ops JSON into the warehouse")
    lo.add_argument("--local-dir", help="read from a local directory instead of S3")
    lo.add_argument("--db", help="local DuckDB file instead of MotherDuck")

    lb = sub.add_parser("load-bronze", help="load authoritative snapshots into bronze")
    lb.add_argument("--local-dir", help="read from a local directory instead of S3")
    lb.add_argument("--db", help="local DuckDB file instead of MotherDuck")

    bs = sub.add_parser("build-silver", help="run the dbt silver models and tests")
    bs.add_argument("--db", help="local DuckDB file instead of MotherDuck")

    ex = sub.add_parser("extract", help="LLM extraction for postings in target role families")
    ex.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    ex.add_argument("--limit", type=int, help="process at most N postings")
    ex.add_argument("--cap", type=float, help="spend cap in USD (default from extraction.yaml)")
    ex.add_argument("--backfill", action="store_true", help="use the backfill spend cap")
    ex.add_argument("--dry-run", action="store_true", help="count and project cost; no API calls")
    ex.add_argument(
        "--batch", choices=["submit", "collect"], help="use the Batch API (first pass only)"
    )

    fr = sub.add_parser("check-freshness", help="fail if any source is stale")
    fr.add_argument("--local-dir", help="read from a local directory instead of S3")

    va = sub.add_parser("validate-employers", help="probe every seeded board")
    va.add_argument("--write", action="store_true", help="refresh counts in employers.yaml")
    return p


def main(argv: Sequence[str] | None = None, clock: Clock | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    clock = clock or utc_now
    handlers = {
        "ingest": cmd_ingest,
        "load-ops": cmd_load_ops,
        "load-bronze": cmd_load_bronze,
        "build-silver": cmd_build_silver,
        "extract": cmd_extract,
        "check-freshness": cmd_check_freshness,
        "validate-employers": cmd_validate_employers,
    }
    return handlers[args.command](args, clock)


if __name__ == "__main__":
    raise SystemExit(main())
