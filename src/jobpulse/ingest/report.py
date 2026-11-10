"""Run report: every failed company with its reason code. Rendered as JSON (S3) and Markdown
(GitHub step summary)."""

from __future__ import annotations

from typing import Any

from jobpulse.ingest.manifest import SnapshotStatus
from jobpulse.ingest.runner import RunResult


def build_report(run: RunResult) -> dict[str, Any]:
    complete = [c for c in run.companies if c.status is SnapshotStatus.COMPLETE]
    return {
        "attempt_id": run.attempt_id,
        "snapshot_date": run.snapshot_date.isoformat(),
        "env": run.env,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat(),
        "companies_total": len(run.companies),
        "companies_complete": len(complete),
        "companies_authoritative": sum(c.is_authoritative for c in run.companies),
        "companies_failed": len(run.failed),
        "success_ratio": round(run.success_ratio, 4),
        "records_kept": sum(c.record_count for c in complete),
        "failed": [
            {
                "company": c.company,
                "source_id": c.source_id,
                "board_id": c.board_id,
                "status": c.status.value,
                "reason_code": c.reason_code,
                "detail": c.detail,
            }
            for c in sorted(run.failed, key=lambda c: (c.source_id, c.board_id))
        ],
    }


def _cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")[:200]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"## Ingest {report['snapshot_date']} · `{report['attempt_id']}`",
        "",
        f"- Complete snapshots: **{report['companies_complete']}/{report['companies_total']}** "
        f"({report['success_ratio']:.1%})",
        f"- Authoritative pointers written/replaced: {report['companies_authoritative']}",
        f"- Records kept: {report['records_kept']}",
        f"- Failed companies: **{report['companies_failed']}**",
        "",
    ]
    if report["failed"]:
        lines += [
            "| Company | Source | Board | Status | Reason | Detail |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {_cell(f['company'])} | {f['source_id']} | `{f['board_id']}` | {f['status']} "
            f"| `{f['reason_code']}` | {_cell(f['detail'])} |"
            for f in report["failed"]
        ]
    else:
        lines.append("No failed companies.")
    return "\n".join(lines) + "\n"
