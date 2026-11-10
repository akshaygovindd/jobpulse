"""Bronze loader: copy the data named by the authoritative pointers into the warehouse.

Reads **pointers only**. It lists `snapshots/` (never `raw/`) to find logical snapshots, follows
each pointer to its manifest, re-verifies the manifest and page hashes, and loads that attempt.
When a pointer moves to a newer attempt, that company-day's rows are replaced in one
transaction, so row counts for a logical snapshot never change. A failure for one company-day is
recorded with a reason and never stops the others.
"""

from __future__ import annotations

import gzip
import json
import tempfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
from pydantic import ValidationError

from jobpulse.bronze.normalize import NormalizedPosting, extract_jobs, normalize_posting
from jobpulse.config import WarehouseConfig
from jobpulse.ingest.identity import Clock
from jobpulse.ingest.manifest import Manifest, Pointer, SnapshotStatus
from jobpulse.ingest.pointer import PointerError, verify_pages
from jobpulse.ingest.storage import Storage, StorageError, canonical_json, sha256_hex
from jobpulse.logging import get_logger
from jobpulse.ops.load_ops import transaction

POINTER_SUFFIX = "/_AUTHORITATIVE.json"

_RAW_POSTINGS_DDL = """
    source_id VARCHAR NOT NULL, board_id VARCHAR NOT NULL, snapshot_date DATE NOT NULL,
    attempt_id VARCHAR NOT NULL, external_posting_id VARCHAR NOT NULL, title VARCHAR,
    location_raw VARCHAR, description_text VARCHAR, apply_url VARCHAR,
    content_hash VARCHAR NOT NULL, payload JSON, object_key VARCHAR NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL"""
_POINTERS_DDL = """
    source_id VARCHAR NOT NULL, board_id VARCHAR NOT NULL, company VARCHAR NOT NULL,
    snapshot_date DATE NOT NULL, attempt_id VARCHAR NOT NULL, manifest_key VARCHAR NOT NULL,
    manifest_sha256 VARCHAR NOT NULL, record_count INTEGER NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL, loaded_at TIMESTAMPTZ NOT NULL"""

_NDJSON_COLUMNS = (
    "{'source_id':'VARCHAR','board_id':'VARCHAR','snapshot_date':'DATE','attempt_id':'VARCHAR',"
    "'external_posting_id':'VARCHAR','title':'VARCHAR','location_raw':'VARCHAR',"
    "'description_text':'VARCHAR','apply_url':'VARCHAR','content_hash':'VARCHAR',"
    "'payload':'VARCHAR','object_key':'VARCHAR'}"
)


class BronzeLoadError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class FailedSnapshot:
    pointer_key: str
    reason: str
    detail: str


@dataclass
class BronzeResult:
    pointers_seen: int = 0
    loaded: int = 0
    skipped_unchanged: int = 0
    failed: list[FailedSnapshot] = field(default_factory=list)
    rows_loaded: int = 0
    rows_by_source: Counter[str] = field(default_factory=Counter)


def ensure_bronze_tables(con: duckdb.DuckDBPyConnection, cfg: WarehouseConfig) -> None:
    con.execute(
        f'CREATE TABLE IF NOT EXISTS "{cfg.bronze_schema}"."raw_postings" ({_RAW_POSTINGS_DDL})'
    )
    con.execute(
        f'CREATE TABLE IF NOT EXISTS "{cfg.bronze_schema}"."snapshot_pointers" ({_POINTERS_DDL})'
    )


def _read_attempt(
    storage: Storage, pointer: Pointer
) -> tuple[Manifest, list[tuple[NormalizedPosting, dict[str, Any], str]]]:
    raw = storage.get(pointer.manifest_key)
    if raw is None:
        raise BronzeLoadError("manifest_missing", pointer.manifest_key)
    try:
        manifest = Manifest.model_validate_json(raw)
    except ValidationError as e:
        raise BronzeLoadError("manifest_invalid", str(e)) from e
    if manifest.status is not SnapshotStatus.COMPLETE:
        raise BronzeLoadError("manifest_not_complete", f"status={manifest.status.value}")
    if sha256_hex(canonical_json(manifest.model_dump(mode="json"))) != pointer.manifest_sha256:
        raise BronzeLoadError("manifest_hash_mismatch", pointer.manifest_key)
    if (manifest.source_id, manifest.board_id, manifest.snapshot_date, manifest.attempt_id) != (
        pointer.source_id,
        pointer.board_id,
        pointer.snapshot_date,
        pointer.attempt_id,
    ):
        raise BronzeLoadError("pointer_manifest_mismatch", pointer.manifest_key)
    try:
        verify_pages(storage, manifest)
    except (PointerError, OSError, EOFError, zlib.error) as e:  # BadGzipFile is an OSError
        raise BronzeLoadError("page_verification_failed", f"{type(e).__name__}: {e}") from e

    postings: list[tuple[NormalizedPosting, dict[str, Any], str]] = []
    seen: set[str] = set()
    for page in manifest.pages:
        body = storage.get(page.key)
        if body is None:  # verify_pages just read it; this guards a race
            raise BronzeLoadError("page_missing", page.key)
        try:
            jobs = extract_jobs(manifest.source_id, json.loads(gzip.decompress(body)))
            for job in jobs:
                posting = normalize_posting(manifest.source_id, job)
                if posting.external_posting_id in seen:
                    raise BronzeLoadError("duplicate_posting_id", posting.external_posting_id)
                seen.add(posting.external_posting_id)
                postings.append((posting, job, page.key))
        except (ValueError, KeyError, TypeError) as e:
            if isinstance(e, BronzeLoadError):
                raise
            raise BronzeLoadError("parse_error", f"{page.key}: {e}") from e
    if len(postings) != pointer.record_count or len(postings) != manifest.record_count:
        raise BronzeLoadError(
            "record_count_mismatch",
            f"pointer={pointer.record_count} manifest={manifest.record_count} read={len(postings)}",
        )
    return manifest, postings


def _write_ndjson(
    path: Path, pointer: Pointer, postings: list[tuple[NormalizedPosting, dict[str, Any], str]]
) -> None:
    with path.open("w", encoding="utf-8") as f:
        for posting, job, object_key in postings:
            record = {
                "source_id": pointer.source_id,
                "board_id": pointer.board_id,
                "snapshot_date": pointer.snapshot_date.isoformat(),
                "attempt_id": pointer.attempt_id,
                "external_posting_id": posting.external_posting_id,
                "title": posting.title,
                "location_raw": posting.location_raw,
                "description_text": posting.description_text,
                "apply_url": posting.apply_url,
                "content_hash": posting.content_hash,
                "payload": json.dumps(job, sort_keys=True, ensure_ascii=False),
                "object_key": object_key,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _replace_snapshot(
    con: duckdb.DuckDBPyConnection,
    cfg: WarehouseConfig,
    pointer: Pointer,
    manifest: Manifest,
    postings: list[tuple[NormalizedPosting, dict[str, Any], str]],
    loaded_at: Any,
) -> None:
    key = [pointer.source_id, pointer.board_id, pointer.snapshot_date]
    where = "WHERE source_id = ? AND board_id = ? AND snapshot_date = ?"
    with tempfile.TemporaryDirectory() as tmp, transaction(con):
        con.execute(f'DELETE FROM "{cfg.bronze_schema}"."raw_postings" {where}', key)
        con.execute(f'DELETE FROM "{cfg.bronze_schema}"."snapshot_pointers" {where}', key)
        if postings:
            path = Path(tmp) / "postings.ndjson"
            _write_ndjson(path, pointer, postings)
            con.execute(
                f"""INSERT INTO "{cfg.bronze_schema}"."raw_postings"
                    SELECT source_id, board_id, snapshot_date, attempt_id, external_posting_id,
                           title, location_raw, description_text, apply_url, content_hash,
                           payload::JSON, object_key, ?::TIMESTAMPTZ
                    FROM read_json(?, format='newline_delimited', columns={_NDJSON_COLUMNS})""",
                [loaded_at, str(path)],
            )
        con.execute(
            f'INSERT INTO "{cfg.bronze_schema}"."snapshot_pointers" VALUES (?,?,?,?,?,?,?,?,?,?)',
            [
                pointer.source_id,
                pointer.board_id,
                manifest.company,
                pointer.snapshot_date,
                pointer.attempt_id,
                pointer.manifest_key,
                pointer.manifest_sha256,
                pointer.record_count,
                pointer.completed_at,
                loaded_at,
            ],
        )


def load_bronze(
    storage: Storage,
    con: duckdb.DuckDBPyConnection,
    cfg: WarehouseConfig,
    *,
    clock: Clock,
    attempt_id: str,
) -> BronzeResult:
    ensure_bronze_tables(con, cfg)
    result = BronzeResult()
    loaded_attempts: dict[tuple[str, str, Any], str] = {
        (r[0], r[1], r[2]): r[3]
        for r in con.execute(
            "SELECT source_id, board_id, snapshot_date, attempt_id "
            f'FROM "{cfg.bronze_schema}"."snapshot_pointers"'
        ).fetchall()
    }
    for key in storage.list_keys("snapshots/"):
        if not key.endswith(POINTER_SUFFIX):
            continue
        result.pointers_seen += 1
        log = get_logger(attempt_id=attempt_id, event_scope="bronze", pointer_key=key)
        try:
            raw = storage.get(key)
            if raw is None:
                raise BronzeLoadError("pointer_missing", key)
            try:
                pointer = Pointer.model_validate_json(raw)
            except ValidationError as e:
                raise BronzeLoadError("pointer_invalid", str(e)) from e
            ident = (pointer.source_id, pointer.board_id, pointer.snapshot_date)
            if loaded_attempts.get(ident) == pointer.attempt_id:
                result.skipped_unchanged += 1
                continue
            manifest, postings = _read_attempt(storage, pointer)
            _replace_snapshot(con, cfg, pointer, manifest, postings, clock())
        except BronzeLoadError as e:
            result.failed.append(FailedSnapshot(key, e.reason, e.detail))
            log.warning("bronze_snapshot_failed", reason=e.reason, detail=e.detail)
            continue
        except (StorageError, duckdb.Error) as e:
            result.failed.append(FailedSnapshot(key, "load_error", type(e).__name__))
            log.warning("bronze_snapshot_failed", reason="load_error", detail=str(e)[:200])
            continue
        except Exception as e:  # isolation: nothing may escape past the loader
            result.failed.append(FailedSnapshot(key, "unexpected_error", type(e).__name__))
            log.error("bronze_snapshot_failed", reason="unexpected_error", detail=str(e)[:200])
            continue
        loaded_attempts[ident] = pointer.attempt_id
        result.loaded += 1
        result.rows_loaded += len(postings)
        result.rows_by_source[pointer.source_id] += len(postings)
        log.info(
            "bronze_snapshot_loaded",
            source_id=pointer.source_id,
            company=pointer.board_id,
            snapshot_date=pointer.snapshot_date.isoformat(),
            rows=len(postings),
        )
    return result
