"""Build and write the P1 ops records (pipeline_runs, company_snapshots, source_registry)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from jobpulse.config import SourcesFile
from jobpulse.ingest.keys import SOURCE_REGISTRY_LATEST, ops_key
from jobpulse.ingest.manifest import SnapshotStatus
from jobpulse.ingest.runner import RunResult
from jobpulse.ingest.storage import Storage, get_json, put_json


def company_snapshot_rows(run: RunResult) -> list[dict[str, Any]]:
    return [
        {
            "attempt_id": run.attempt_id,
            "snapshot_date": run.snapshot_date.isoformat(),
            "source_id": c.source_id,
            "board_id": c.board_id,
            "company": c.company,
            "status": c.status.value,
            "reason_code": c.reason_code,
            "detail": c.detail,
            "pages": c.pages,
            "record_count": c.record_count,
            "manifest_key": c.manifest_key,
            "is_authoritative": c.is_authoritative,
            "pointer_outcome": c.pointer_outcome,
            "started_at": c.started_at.isoformat(),
            "finished_at": c.finished_at.isoformat(),
        }
        for c in run.companies
    ]


def pipeline_run_rows(run: RunResult) -> list[dict[str, Any]]:
    rows = []
    for source_id in sorted({c.source_id for c in run.companies}):
        cs = [c for c in run.companies if c.source_id == source_id]
        complete = [c for c in cs if c.status is SnapshotStatus.COMPLETE]
        dropped: Counter[str] = Counter()
        for c in cs:
            if c.status is not SnapshotStatus.COMPLETE:
                dropped[c.reason_code or "unknown"] += c.records_fetched
        if len(complete) == len(cs):
            status = "success"
        elif complete:
            status = "partial"
        else:
            status = "failed"
        rows.append(
            {
                "attempt_id": run.attempt_id,
                "snapshot_date": run.snapshot_date.isoformat(),
                "task": "ingest",
                "source_id": source_id,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat(),
                "duration_s": round((run.finished_at - run.started_at).total_seconds(), 3),
                "status": status,
                "companies_total": len(cs),
                "companies_complete": len(complete),
                "companies_failed": len(cs) - len(complete),
                "rows_in": sum(c.records_fetched for c in cs),
                "rows_kept": sum(c.record_count for c in complete),
                "rows_dropped_json": dict(dropped),
                "llm_cost_usd": 0.0,
                "env": run.env,
            }
        )
    return rows


def source_registry_rows(
    run: RunResult, sources: SourcesFile, previous: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    prev = {r["source_id"]: r for r in previous or []}
    rows = []
    for s in sources.sources:
        row: dict[str, Any] = {
            "source_id": s.source_id,
            "source_name": s.source_name,
            "enabled": s.enabled,
            "attribution_url": s.attribution_url,
            "last_success_at": None,
            "last_failure_at": None,
            "last_cursor": None,  # full-board snapshots: no cursor for ATS sources
            "records_fetched": 0,
        }
        old = prev.get(s.source_id)
        if old:
            for k in ("last_success_at", "last_failure_at", "last_cursor"):
                row[k] = old.get(k)
        cs = [c for c in run.companies if c.source_id == s.source_id]
        if cs:
            row["records_fetched"] = sum(c.records_fetched for c in cs)
            if any(c.status is SnapshotStatus.COMPLETE for c in cs):
                row["last_success_at"] = run.finished_at.isoformat()
            if any(c.status is not SnapshotStatus.COMPLETE for c in cs):
                row["last_failure_at"] = run.finished_at.isoformat()
        rows.append(row)
    return rows


def write_ops(
    storage: Storage, run: RunResult, sources: SourcesFile, report: dict[str, Any]
) -> dict[str, str]:
    """Write all ops JSON for this attempt; returns {table: key}."""
    d, a = run.snapshot_date, run.attempt_id
    previous = get_json(storage, SOURCE_REGISTRY_LATEST)
    registry = source_registry_rows(run, sources, previous)
    written = {
        "pipeline_runs": ops_key("pipeline_runs", d, a),
        "company_snapshots": ops_key("company_snapshots", d, a),
        "source_registry": ops_key("source_registry", d, a),
        "run_reports": ops_key("run_reports", d, a),
    }
    put_json(storage, written["pipeline_runs"], pipeline_run_rows(run))
    put_json(storage, written["company_snapshots"], company_snapshot_rows(run))
    put_json(storage, written["source_registry"], registry)
    put_json(storage, written["run_reports"], report)
    put_json(storage, SOURCE_REGISTRY_LATEST, registry)
    return written
