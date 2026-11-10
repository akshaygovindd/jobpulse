"""Authoritative pointer: one per (source, company, snapshot_date).

Written only for a complete manifest whose pages all verify. A later complete attempt replaces
it; an older or incomplete attempt never does. Runs are serialised by the workflow concurrency
group; the completed_at guard protects against out-of-order publication.
"""

from __future__ import annotations

import gzip
from datetime import datetime
from enum import StrEnum

from jobpulse.ingest.keys import pointer_key
from jobpulse.ingest.manifest import Manifest, Pointer, SnapshotStatus
from jobpulse.ingest.storage import Storage, canonical_json, put_json, sha256_hex


class PointerOutcome(StrEnum):
    PUBLISHED = "published"  # no pointer existed for this day
    REPLACED = "replaced"  # a newer complete attempt replaced the previous pointer
    KEPT_EXISTING = "kept_existing"  # existing pointer is newer or equal; left unchanged


class PointerError(Exception):
    pass


def read_pointer(storage: Storage, key: str) -> Pointer | None:
    raw = storage.get(key)
    return None if raw is None else Pointer.model_validate_json(raw)


def verify_pages(storage: Storage, manifest: Manifest) -> None:
    for page in manifest.pages:
        body = storage.get(page.key)
        if body is None:
            raise PointerError(f"missing page {page.key}")
        if sha256_hex(gzip.decompress(body)) != page.sha256:
            raise PointerError(f"hash mismatch for {page.key}")


def publish_pointer(
    storage: Storage, manifest: Manifest, manifest_key: str, now: datetime
) -> PointerOutcome:
    if manifest.status is not SnapshotStatus.COMPLETE:
        raise PointerError(f"refusing to publish pointer for {manifest.status} manifest")
    stored = storage.get(manifest_key)
    if stored is None:
        raise PointerError(f"manifest {manifest_key} not found")
    if Manifest.model_validate_json(stored) != manifest:
        raise PointerError(f"stored manifest {manifest_key} differs from in-memory manifest")
    verify_pages(storage, manifest)

    key = pointer_key(manifest.source_id, manifest.board_id, manifest.snapshot_date)
    existing = read_pointer(storage, key)
    if existing is not None and existing.completed_at >= manifest.completed_at:
        return PointerOutcome.KEPT_EXISTING

    pointer = Pointer(
        source_id=manifest.source_id,
        board_id=manifest.board_id,
        snapshot_date=manifest.snapshot_date,
        attempt_id=manifest.attempt_id,
        manifest_key=manifest_key,
        manifest_sha256=sha256_hex(canonical_json(manifest.model_dump(mode="json"))),
        record_count=manifest.record_count,
        completed_at=manifest.completed_at,
        published_at=now,
    )
    put_json(storage, key, pointer.model_dump(mode="json"))
    return PointerOutcome.PUBLISHED if existing is None else PointerOutcome.REPLACED
