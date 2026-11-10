from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from jobpulse.freshness import check_freshness, render_markdown
from jobpulse.ingest.keys import pointer_key
from jobpulse.ingest.manifest import Pointer
from jobpulse.ingest.storage import S3Storage, put_json
from tests.conftest import employer

NOW = datetime(2026, 11, 10, 9, 30, tzinfo=UTC)


def put_pointer(storage: S3Storage, source: str, board: str, completed_at: datetime) -> None:
    d: date = completed_at.date()
    p = Pointer(
        source_id=source,
        board_id=board,
        snapshot_date=d,
        attempt_id="gha-1-1",
        manifest_key="raw/x/_MANIFEST.json",
        manifest_sha256="0" * 64,
        record_count=1,
        completed_at=completed_at,
        published_at=completed_at,
    )
    put_json(storage, pointer_key(source, board, d), p.model_dump(mode="json"))


EMPLOYERS = [
    employer("greenhouse", "acme"),
    employer("greenhouse", "globex"),
    employer("lever", "x"),
]


def test_fresh_sources(s3_storage: S3Storage) -> None:
    put_pointer(s3_storage, "greenhouse", "acme", NOW - timedelta(hours=1))
    put_pointer(s3_storage, "greenhouse", "globex", NOW - timedelta(hours=25))
    put_pointer(s3_storage, "lever", "x", NOW - timedelta(hours=2))
    res = check_freshness(s3_storage, EMPLOYERS, now=NOW, max_age_hours=30, lookback_days=3)
    gh = next(r for r in res if r.source_id == "greenhouse")
    assert not gh.stale
    assert gh.age_hours == 1.0
    assert gh.companies_with_recent_pointer == 2
    assert not any(r.stale for r in res)


def test_stale_source_older_than_threshold(s3_storage: S3Storage) -> None:
    put_pointer(s3_storage, "greenhouse", "acme", NOW - timedelta(hours=1))
    put_pointer(s3_storage, "lever", "x", NOW - timedelta(hours=31))
    res = check_freshness(s3_storage, EMPLOYERS, now=NOW, max_age_hours=30, lookback_days=3)
    lever = next(r for r in res if r.source_id == "lever")
    assert lever.stale
    assert "STALE" in render_markdown(res, 30)


def test_no_pointers_is_stale(s3_storage: S3Storage) -> None:
    res = check_freshness(s3_storage, EMPLOYERS, now=NOW, max_age_hours=30, lookback_days=3)
    assert all(r.stale and r.age_hours is None for r in res)


def test_pointer_beyond_lookback_ignored(s3_storage: S3Storage) -> None:
    put_pointer(s3_storage, "lever", "x", NOW - timedelta(days=5))
    res = check_freshness(s3_storage, EMPLOYERS, now=NOW, max_age_hours=30, lookback_days=3)
    lever = next(r for r in res if r.source_id == "lever")
    assert lever.newest_completed_at is None
