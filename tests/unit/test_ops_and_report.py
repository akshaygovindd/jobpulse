from __future__ import annotations

from datetime import date

from jobpulse.config import load_sources
from jobpulse.ingest.keys import SOURCE_REGISTRY_LATEST, ops_key
from jobpulse.ingest.report import build_report, render_markdown
from jobpulse.ingest.runner import run_ingest
from jobpulse.ingest.storage import S3Storage, get_json
from jobpulse.ops.s3_ops import write_ops
from tests.conftest import TickingClock, employer
from tests.unit.test_ingest_idempotency import FakeConnector

DAY = date(2026, 11, 10)


def _run(storage: S3Storage, clock: TickingClock, attempt: str = "gha-100-1") -> dict[str, str]:
    result = run_ingest(
        [
            employer("greenhouse", "acme", "Acme"),
            employer("greenhouse", "missing", "Missing Co"),
            employer("lever", "initech", "Initech"),
        ],
        {"greenhouse": FakeConnector(), "lever": FakeConnector("lever", jobs=[1])},
        storage,
        snapshot_date=DAY,
        attempt_id=attempt,
        env="gha",
        clock=clock,
    )
    return write_ops(storage, result, load_sources(), build_report(result))


def test_ops_json_written(s3_storage: S3Storage, clock: TickingClock) -> None:
    keys = _run(s3_storage, clock)
    assert keys["pipeline_runs"] == ops_key("pipeline_runs", DAY, "gha-100-1")

    runs = get_json(s3_storage, keys["pipeline_runs"])
    by_source = {r["source_id"]: r for r in runs}
    gh = by_source["greenhouse"]
    assert gh["status"] == "partial"
    assert (gh["companies_total"], gh["companies_complete"], gh["companies_failed"]) == (2, 1, 1)
    assert gh["rows_in"] == 3
    assert gh["rows_kept"] == 3
    assert gh["rows_dropped_json"] == {"board_not_found": 0}
    assert by_source["lever"]["status"] == "success"

    snaps = get_json(s3_storage, keys["company_snapshots"])
    missing = next(s for s in snaps if s["board_id"] == "missing")
    assert (missing["status"], missing["reason_code"]) == ("failed", "board_not_found")
    assert missing["is_authoritative"] is False

    registry = {r["source_id"]: r for r in get_json(s3_storage, SOURCE_REGISTRY_LATEST)}
    assert registry["greenhouse"]["last_success_at"] is not None
    assert registry["greenhouse"]["last_failure_at"] is not None
    assert registry["lever"]["last_failure_at"] is None
    assert registry["ashby"]["enabled"] is False
    assert registry["adzuna"]["attribution_url"] == "https://www.adzuna.com"


def test_registry_carries_forward_previous_state(
    s3_storage: S3Storage, clock: TickingClock
) -> None:
    _run(s3_storage, clock, "gha-100-1")
    first = {r["source_id"]: r for r in get_json(s3_storage, SOURCE_REGISTRY_LATEST)}
    _run(s3_storage, clock, "gha-101-1")
    second = {r["source_id"]: r for r in get_json(s3_storage, SOURCE_REGISTRY_LATEST)}
    assert second["lever"]["last_success_at"] > first["lever"]["last_success_at"]
    assert second["lever"]["last_failure_at"] is None


def test_report_lists_every_failed_company(s3_storage: S3Storage, clock: TickingClock) -> None:
    keys = _run(s3_storage, clock)
    report = get_json(s3_storage, keys["run_reports"])
    assert report["companies_total"] == 3
    assert report["companies_failed"] == 1
    assert report["failed"] == [
        {
            "company": "Missing Co",
            "source_id": "greenhouse",
            "board_id": "missing",
            "status": "failed",
            "reason_code": "board_not_found",
            "detail": "HTTP 404",
        }
    ]
    md = render_markdown(report)
    assert "| Missing Co | greenhouse | `missing` | failed | `board_not_found` | HTTP 404 |" in md
    assert "2/3" in md
