"""Snapshot identity: append-only attempts, one authoritative pointer per (source, company, date).

Covers ROADMAP P1 idempotency checks:
  (i)   re-run of the same workflow run (attempt 2)
  (ii)  a new dispatch on the same date
  (iii) an incomplete later attempt never moves the pointer
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import date

import pytest

from jobpulse.connectors.base import Page
from jobpulse.http import FetchError, ReasonCode
from jobpulse.ingest.identity import SnapshotDateError
from jobpulse.ingest.keys import pointer_key
from jobpulse.ingest.manifest import Manifest, SnapshotStatus
from jobpulse.ingest.pointer import read_pointer
from jobpulse.ingest.runner import RunResult, run_ingest
from jobpulse.ingest.storage import S3Storage
from tests.conftest import TickingClock, employer

DAY = date(2026, 11, 10)


class FakeConnector:
    """Yields `pages` pages of jobs; optionally fails after `fail_after` pages."""

    def __init__(
        self,
        source_id: str = "greenhouse",
        *,
        jobs: list[int] | None = None,
        pages: int = 1,
        fail_after: int | None = None,
        reason: ReasonCode = ReasonCode.RATE_LIMITED,
    ) -> None:
        self.source_id = source_id
        self.jobs = jobs if jobs is not None else [1, 2, 3]
        self.pages = pages
        self.fail_after = fail_after
        self.reason = reason

    def fetch_company(self, board_id: str) -> Iterator[Page]:
        if board_id == "missing":
            raise FetchError(ReasonCode.BOARD_NOT_FOUND, "HTTP 404")
        for n in range(1, self.pages + 1):
            if self.fail_after is not None and n > self.fail_after:
                raise FetchError(self.reason, f"failed on page {n}")
            ids = [f"{board_id}-{n}-{j}" for j in self.jobs]
            yield Page(n, {"jobs": [{"id": i} for i in ids]}, tuple(ids))


def run(
    storage: S3Storage, clock: TickingClock, attempt_id: str, connector: FakeConnector, *boards: str
) -> RunResult:
    return run_ingest(
        [employer("greenhouse", b) for b in boards or ("acme",)],
        {"greenhouse": connector},
        storage,
        snapshot_date=DAY,
        attempt_id=attempt_id,
        env="local",
        clock=clock,
    )


def attempt_prefixes(storage: S3Storage, board: str = "acme") -> set[str]:
    keys = storage.list_keys(f"raw/source=greenhouse/company={board}/dt={DAY}/")
    return {k.split("/")[4] for k in keys}


def pointer_keys(storage: S3Storage) -> list[str]:
    return storage.list_keys("snapshots/")


def test_complete_attempt_writes_pages_manifest_and_pointer(
    s3_storage: S3Storage, clock: TickingClock
) -> None:
    result = run(s3_storage, clock, "gha-100-1", FakeConnector(pages=2))
    c = result.companies[0]
    assert c.status is SnapshotStatus.COMPLETE
    assert c.is_authoritative
    assert c.pointer_outcome == "published"

    p = read_pointer(s3_storage, pointer_key("greenhouse", "acme", DAY))
    assert p is not None
    assert p.attempt_id == "gha-100-1"
    assert p.record_count == 6

    raw = s3_storage.get(p.manifest_key)
    assert raw is not None
    manifest = Manifest.model_validate_json(raw)
    assert manifest.status is SnapshotStatus.COMPLETE
    assert [pg.key.rsplit("/", 1)[1] for pg in manifest.pages] == [
        "page=0001.json.gz",
        "page=0002.json.gz",
    ]
    obj = s3_storage._s3.head_object(Bucket=s3_storage.bucket, Key=manifest.pages[0].key)
    assert obj["Metadata"]["sha256"] == manifest.pages[0].sha256
    body = s3_storage.get(manifest.pages[0].key)
    assert body is not None
    assert json.loads(gzip.decompress(body))["jobs"][0]["id"] == "acme-1-1"


def test_i_rerun_same_workflow_run_attempt_2(s3_storage: S3Storage, clock: TickingClock) -> None:
    run(s3_storage, clock, "gha-100-1", FakeConnector())
    run(s3_storage, clock, "gha-100-2", FakeConnector())

    assert attempt_prefixes(s3_storage) == {"attempt=gha-100-1", "attempt=gha-100-2"}
    assert pointer_keys(s3_storage) == [
        f"snapshots/source=greenhouse/company=acme/dt={DAY}/_AUTHORITATIVE.json"
    ]
    p = read_pointer(s3_storage, pointer_keys(s3_storage)[0])
    assert p is not None
    assert p.attempt_id == "gha-100-2"


def test_i_rerun_attempt_2_incomplete_keeps_attempt_1(
    s3_storage: S3Storage, clock: TickingClock
) -> None:
    run(s3_storage, clock, "gha-100-1", FakeConnector(pages=2))
    r2 = run(s3_storage, clock, "gha-100-2", FakeConnector(pages=2, fail_after=1))

    assert r2.companies[0].status is SnapshotStatus.INCOMPLETE
    assert attempt_prefixes(s3_storage) == {"attempt=gha-100-1", "attempt=gha-100-2"}
    assert len(pointer_keys(s3_storage)) == 1
    p = read_pointer(s3_storage, pointer_keys(s3_storage)[0])
    assert p is not None
    assert p.attempt_id == "gha-100-1"


def test_ii_new_dispatch_same_date(s3_storage: S3Storage, clock: TickingClock) -> None:
    run(s3_storage, clock, "gha-100-1", FakeConnector())
    run(s3_storage, clock, "gha-200-1", FakeConnector(jobs=[1, 2, 3, 4]))

    assert attempt_prefixes(s3_storage) == {"attempt=gha-100-1", "attempt=gha-200-1"}
    assert len(pointer_keys(s3_storage)) == 1
    p = read_pointer(s3_storage, pointer_keys(s3_storage)[0])
    assert p is not None
    assert (p.attempt_id, p.record_count) == ("gha-200-1", 4)


@pytest.mark.parametrize("pages_before_failure", [0, 1])
def test_iii_incomplete_later_attempt_leaves_pointer(
    s3_storage: S3Storage, clock: TickingClock, pages_before_failure: int
) -> None:
    run(s3_storage, clock, "gha-100-1", FakeConnector(pages=2))
    before = s3_storage.get(pointer_key("greenhouse", "acme", DAY))
    r = run(
        s3_storage,
        clock,
        "gha-300-1",
        FakeConnector(pages=2, fail_after=pages_before_failure, reason=ReasonCode.TIMEOUT),
    )
    c = r.companies[0]
    assert c.status in (SnapshotStatus.FAILED, SnapshotStatus.INCOMPLETE)
    assert c.reason_code == "timeout"
    assert not c.is_authoritative
    assert s3_storage.get(pointer_key("greenhouse", "acme", DAY)) == before

    # The failed attempt still leaves an append-only record with its manifest.
    raw = s3_storage.get(c.manifest_key or "")
    assert raw is not None
    assert Manifest.model_validate_json(raw).status is c.status


def test_incomplete_first_attempt_writes_no_pointer(
    s3_storage: S3Storage, clock: TickingClock
) -> None:
    run(s3_storage, clock, "gha-100-1", FakeConnector(pages=3, fail_after=2))
    assert pointer_keys(s3_storage) == []


def test_one_bad_board_does_not_stop_the_run(s3_storage: S3Storage, clock: TickingClock) -> None:
    r = run(s3_storage, clock, "gha-100-1", FakeConnector(), "acme", "missing", "globex")
    by_board = {c.board_id: c for c in r.companies}
    assert by_board["missing"].status is SnapshotStatus.FAILED
    assert by_board["missing"].reason_code == "board_not_found"
    assert by_board["acme"].is_authoritative
    assert by_board["globex"].is_authoritative
    assert [c.board_id for c in r.failed] == ["missing"]
    assert len(pointer_keys(s3_storage)) == 2


def test_unexpected_exception_is_isolated(s3_storage: S3Storage, clock: TickingClock) -> None:
    class Boom(FakeConnector):
        def fetch_company(self, board_id: str) -> Iterator[Page]:
            if board_id == "boom":
                raise RuntimeError("kaboom")
            yield from super().fetch_company(board_id)

    r = run(s3_storage, clock, "gha-100-1", Boom(), "boom", "acme")
    by_board = {c.board_id: c for c in r.companies}
    assert by_board["boom"].reason_code == "unexpected_error"
    assert by_board["acme"].is_authoritative


def test_runner_rejects_non_today_snapshot_date(s3_storage: S3Storage, clock: TickingClock) -> None:
    for bad in (date(2026, 11, 9), date(2026, 11, 11)):
        with pytest.raises(SnapshotDateError):
            run_ingest(
                [employer("greenhouse", "acme")],
                {"greenhouse": FakeConnector()},
                s3_storage,
                snapshot_date=bad,
                attempt_id="gha-1-1",
                env="local",
                clock=clock,
            )
    assert s3_storage.list_keys("") == []
