"""Ingest runner: fetch every employer board, write raw pages + manifest, publish pointers.

Isolation rule: a failure for one company or source is caught, recorded with a reason code,
and never stops the run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

from jobpulse.config import Employer
from jobpulse.connectors.base import Connector
from jobpulse.http import FetchError, ReasonCode
from jobpulse.ingest.identity import Clock, SnapshotDateError
from jobpulse.ingest.keys import manifest_key, page_key
from jobpulse.ingest.manifest import Manifest, PageEntry, SnapshotStatus
from jobpulse.ingest.pointer import PointerError, PointerOutcome, publish_pointer
from jobpulse.ingest.storage import (
    Storage,
    StorageError,
    canonical_json,
    gzip_deterministic,
    put_json,
    sha256_hex,
)
from jobpulse.logging import get_logger


@dataclass(frozen=True)
class CompanyResult:
    company: str
    source_id: str
    board_id: str
    status: SnapshotStatus
    reason_code: str | None
    detail: str | None
    pages: int
    records_fetched: int
    record_count: int  # records in the manifest (== records_fetched when complete)
    manifest_key: str | None
    is_authoritative: bool
    pointer_outcome: str | None
    started_at: datetime
    finished_at: datetime


@dataclass
class RunResult:
    attempt_id: str
    snapshot_date: date
    env: str
    started_at: datetime
    finished_at: datetime
    companies: list[CompanyResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CompanyResult]:
        return [c for c in self.companies if c.status is not SnapshotStatus.COMPLETE]

    @property
    def success_ratio(self) -> float:
        if not self.companies:
            return 0.0
        complete = sum(c.status is SnapshotStatus.COMPLETE for c in self.companies)
        return complete / len(self.companies)


def _ingest_company(
    employer: Employer,
    connector: Connector,
    storage: Storage,
    snapshot_date: date,
    attempt_id: str,
    clock: Clock,
) -> CompanyResult:
    log = get_logger(
        attempt_id=attempt_id,
        snapshot_date=snapshot_date.isoformat(),
        source_id=employer.source,
        company=employer.board_id,
    )
    started = clock()
    pages: list[PageEntry] = []
    records_fetched = 0
    status = SnapshotStatus.COMPLETE
    reason: str | None = None
    detail: str | None = None

    try:
        for page in connector.fetch_company(employer.board_id):
            key = page_key(
                employer.source, employer.board_id, snapshot_date, attempt_id, page.number
            )
            raw = canonical_json(page.payload)
            digest = sha256_hex(raw)
            storage.put(
                key,
                gzip_deterministic(raw),
                content_type="application/json",
                content_encoding="gzip",
                metadata={"sha256": digest, "records": str(len(page.external_ids))},
            )
            pages.append(PageEntry(key=key, sha256=digest, record_count=len(page.external_ids)))
            records_fetched += len(page.external_ids)
    except FetchError as e:
        reason, detail = e.reason.value, e.detail
    except StorageError as e:
        reason, detail = ReasonCode.STORAGE_ERROR.value, str(e)
    except Exception as e:
        reason, detail = ReasonCode.UNEXPECTED_ERROR.value, f"{type(e).__name__}: {e}"
    if reason is not None:
        status = SnapshotStatus.INCOMPLETE if pages else SnapshotStatus.FAILED
        log.warning("company_fetch_failed", status=status.value, reason=reason, detail=detail)

    m_key = manifest_key(employer.source, employer.board_id, snapshot_date, attempt_id)
    manifest = Manifest(
        source_id=employer.source,
        board_id=employer.board_id,
        company=employer.company,
        snapshot_date=snapshot_date,
        attempt_id=attempt_id,
        status=status,
        reason_code=reason,
        detail=detail,
        pages=pages,
        record_count=sum(p.record_count for p in pages),
        started_at=started,
        completed_at=clock(),
    )
    is_authoritative = False
    outcome: str | None = None
    manifest_written = False
    try:
        put_json(storage, m_key, manifest.model_dump(mode="json"))  # manifest is written LAST
        manifest_written = True
        if status is SnapshotStatus.COMPLETE:
            result = publish_pointer(storage, manifest, m_key, clock())
            outcome = result.value
            # KEPT_EXISTING: a newer complete attempt already owns the day; this one does not.
            is_authoritative = result is not PointerOutcome.KEPT_EXISTING
    except (StorageError, PointerError) as e:
        # Without a verified pointer this attempt cannot count as complete.
        status = SnapshotStatus.INCOMPLETE if pages else SnapshotStatus.FAILED
        reason, detail = ReasonCode.STORAGE_ERROR.value, str(e)
        is_authoritative = False
        log.error("company_publish_failed", reason=reason, detail=detail)

    finished = clock()
    log.info(
        "company_done",
        status=status.value,
        reason=reason,
        records=records_fetched,
        pages=len(pages),
        pointer=outcome,
    )
    return CompanyResult(
        company=employer.company,
        source_id=employer.source,
        board_id=employer.board_id,
        status=status,
        reason_code=reason,
        detail=detail,
        pages=len(pages),
        records_fetched=records_fetched,
        record_count=manifest.record_count,
        manifest_key=m_key if manifest_written else None,
        is_authoritative=is_authoritative,
        pointer_outcome=outcome,
        started_at=started,
        finished_at=finished,
    )


def run_ingest(
    employers: Sequence[Employer],
    connectors: Mapping[str, Connector],
    storage: Storage,
    *,
    snapshot_date: date,
    attempt_id: str,
    env: str,
    clock: Clock,
    max_workers: int = 4,
) -> RunResult:
    started = clock()
    today = started.date()
    if snapshot_date != today:
        # Defence in depth: the CLI and the workflow validate this too.
        raise SnapshotDateError(
            f"snapshot_date {snapshot_date} != current UTC date {today}; missed days stay gaps"
        )
    log = get_logger(attempt_id=attempt_id, snapshot_date=snapshot_date.isoformat())
    log.info("ingest_start", companies=len(employers), env=env)

    def _one(e: Employer) -> CompanyResult:
        connector = connectors.get(e.source)
        if connector is None:
            now = clock()
            return CompanyResult(
                company=e.company,
                source_id=e.source,
                board_id=e.board_id,
                status=SnapshotStatus.FAILED,
                reason_code=ReasonCode.UNEXPECTED_ERROR.value,
                detail=f"no connector for source {e.source}",
                pages=0,
                records_fetched=0,
                record_count=0,
                manifest_key=None,
                is_authoritative=False,
                pointer_outcome=None,
                started_at=now,
                finished_at=now,
            )
        return _ingest_company(e, connector, storage, snapshot_date, attempt_id, clock)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        companies = list(pool.map(_one, employers))

    result = RunResult(
        attempt_id=attempt_id,
        snapshot_date=snapshot_date,
        env=env,
        started_at=started,
        finished_at=clock(),
        companies=companies,
    )
    log.info(
        "ingest_done",
        authoritative=sum(c.is_authoritative for c in companies),
        failed=len(result.failed),
        success_ratio=round(result.success_ratio, 3),
    )
    return result
