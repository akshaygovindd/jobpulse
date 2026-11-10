"""Per-attempt manifest (written LAST) and the authoritative pointer models."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SnapshotStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"  # some pages written, then a failure
    FAILED = "failed"  # nothing usable written


class PageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    sha256: str  # sha256 of the canonical (uncompressed) JSON
    record_count: int


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    source_id: str
    board_id: str
    company: str
    snapshot_date: date
    attempt_id: str
    status: SnapshotStatus
    reason_code: str | None = None
    detail: str | None = None
    pages: list[PageEntry]
    record_count: int
    started_at: datetime
    completed_at: datetime


class Pointer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    source_id: str
    board_id: str
    snapshot_date: date
    attempt_id: str
    manifest_key: str
    manifest_sha256: str
    record_count: int
    completed_at: datetime
    published_at: datetime
