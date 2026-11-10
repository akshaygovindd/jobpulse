from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from jobpulse.ingest.keys import manifest_key, page_key, pointer_key
from jobpulse.ingest.manifest import Manifest, PageEntry, SnapshotStatus
from jobpulse.ingest.pointer import PointerError, PointerOutcome, publish_pointer, read_pointer
from jobpulse.ingest.storage import (
    S3Storage,
    canonical_json,
    gzip_deterministic,
    put_json,
    sha256_hex,
)

DAY = date(2026, 11, 10)
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def write_attempt(
    storage: S3Storage, attempt: str, completed_at: datetime, *, corrupt: bool = False
) -> tuple[Manifest, str]:
    payload = {"jobs": [{"id": 1}]}
    raw = canonical_json(payload)
    key = page_key("greenhouse", "acme", DAY, attempt, 1)
    storage.put(
        key,
        gzip_deterministic(b"tampered" if corrupt else raw),
        content_type="application/json",
    )
    m = Manifest(
        source_id="greenhouse",
        board_id="acme",
        company="Acme",
        snapshot_date=DAY,
        attempt_id=attempt,
        status=SnapshotStatus.COMPLETE,
        pages=[PageEntry(key=key, sha256=sha256_hex(raw), record_count=1)],
        record_count=1,
        started_at=completed_at - timedelta(seconds=5),
        completed_at=completed_at,
    )
    mk = manifest_key("greenhouse", "acme", DAY, attempt)
    put_json(storage, mk, m.model_dump(mode="json"))
    return m, mk


def test_older_attempt_never_replaces_newer(s3_storage: S3Storage) -> None:
    newer, newer_key = write_attempt(s3_storage, "gha-2-1", T0 + timedelta(minutes=5))
    older, older_key = write_attempt(s3_storage, "gha-1-1", T0)
    assert publish_pointer(s3_storage, newer, newer_key, T0) is PointerOutcome.PUBLISHED
    assert publish_pointer(s3_storage, older, older_key, T0) is PointerOutcome.KEPT_EXISTING
    p = read_pointer(s3_storage, pointer_key("greenhouse", "acme", DAY))
    assert p is not None
    assert p.attempt_id == "gha-2-1"


def test_hash_mismatch_refuses_pointer(s3_storage: S3Storage) -> None:
    m, mk = write_attempt(s3_storage, "gha-1-1", T0, corrupt=True)
    with pytest.raises(PointerError, match="hash mismatch"):
        publish_pointer(s3_storage, m, mk, T0)
    assert s3_storage.get(pointer_key("greenhouse", "acme", DAY)) is None


def test_incomplete_manifest_refused(s3_storage: S3Storage) -> None:
    m, mk = write_attempt(s3_storage, "gha-1-1", T0)
    bad = m.model_copy(update={"status": SnapshotStatus.INCOMPLETE})
    with pytest.raises(PointerError, match="refusing"):
        publish_pointer(s3_storage, bad, mk, T0)


def test_missing_manifest_refused(s3_storage: S3Storage) -> None:
    m, _ = write_attempt(s3_storage, "gha-1-1", T0)
    with pytest.raises(PointerError, match="not found"):
        publish_pointer(s3_storage, m, "raw/nope/_MANIFEST.json", T0)
