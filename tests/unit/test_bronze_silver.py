"""P2 fixture tests: bronze loader (pointers only) and dbt silver (closure, versions, dedup).

ROADMAP P2 done-when:
  (a) absent from 1 authoritative snapshot -> still active
  (b) absent from 2 consecutive authoritative snapshots -> closed
  (c) a date with no pointer between them -> not closed
  (e) content change -> new version row, same posting_key
  (f) pointer replaced on the same day -> bronze and silver row counts unchanged
  and: running the full pipeline twice leaves row counts unchanged.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jobpulse.ingest.storage import LocalStorage
from tests.warehouse_helpers import (
    ashby_job,
    counts,
    gh_job,
    ingest_day,
    query,
    run_pipeline,
)

D1, D2, D3, D4, D5 = (date(2026, 11, d) for d in (10, 11, 12, 13, 14))


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "s3")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "wh.duckdb"


def status(db: Path, external_id: str) -> tuple[str, date | None, str | None]:
    rows = query(
        db,
        "SELECT source_status, cast(closed_at AS DATE), closure_reason FROM silver.dim_postings "
        f"WHERE external_posting_id = '{external_id}'",
    )
    assert len(rows) == 1
    return rows[0]


def test_a_absent_from_one_snapshot_is_still_active(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("2")]})
    bronze, silver = run_pipeline(storage, db)
    assert not bronze.failed
    assert silver.success, silver.failures
    assert silver.tests_passed > 0
    assert status(db, "1") == ("active", None, None)
    assert status(db, "2") == ("active", None, None)


def test_b_absent_from_two_consecutive_snapshots_is_closed(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("2")]})
    ingest_day(storage, D3, "greenhouse", {"acme": [gh_job("2")]})
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert status(db, "1") == ("closed", D3, "absent_2_snapshots")
    assert status(db, "2")[0] == "active"


def test_c_date_without_pointer_between_absences_is_not_closed(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("2")]})
    # D3: no ingest at all, so no pointer. D4: absent again.
    ingest_day(storage, D4, "greenhouse", {"acme": [gh_job("2")]})
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert status(db, "1")[0] == "active"
    dates = query(
        db,
        "SELECT snapshot_date FROM silver.fct_posting_observations "
        "WHERE posting_key IN (SELECT posting_key FROM silver.dim_postings "
        "WHERE external_posting_id = '1') AND NOT observed ORDER BY 1",
    )
    assert [r[0] for r in dates] == [D2, D4]  # D3 is not recorded as an absence


def test_c_failed_fetch_day_has_no_pointer_and_does_not_count(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("2")]})
    ingest_day(storage, D3, "greenhouse", {"acme": None})  # rate limited: no pointer
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert status(db, "1")[0] == "active"


def test_empty_complete_board_counts_as_an_authoritative_absence(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")]})
    ingest_day(storage, D2, "greenhouse", {"acme": []})
    ingest_day(storage, D3, "greenhouse", {"acme": []})
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert status(db, "1") == ("closed", D3, "absent_2_snapshots")


def test_reappearing_posting_is_active_again(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("2")]})
    ingest_day(storage, D3, "greenhouse", {"acme": [gh_job("2")]})
    ingest_day(storage, D4, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert status(db, "1") == ("active", None, None)


def test_e_content_change_adds_a_version_with_the_same_posting_key(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1", content="v1")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("1", content="v2")]})
    ingest_day(storage, D3, "greenhouse", {"acme": [gh_job("1", content="v2")]})  # unchanged
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    versions = query(
        db,
        "SELECT posting_key, valid_from, valid_to FROM silver.dim_posting_versions ORDER BY 2",
    )
    assert [(v[1], v[2]) for v in versions] == [(D1, D2), (D2, None)]
    assert len({v[0] for v in versions}) == 1
    postings = query(db, "SELECT posting_key FROM silver.dim_postings")
    assert [p[0] for p in postings] == [versions[0][0]]


def test_f_pointer_replaced_same_day_leaves_counts_unchanged(
    storage: LocalStorage, db: Path
) -> None:
    jobs = [gh_job("1"), gh_job("2")]
    ingest_day(storage, D1, "greenhouse", {"acme": jobs}, attempt_id="local-a")
    ingest_day(storage, D2, "greenhouse", {"acme": jobs}, attempt_id="local-b")
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    before = counts(db)

    # A later complete attempt on D2 replaces the pointer.
    ingest_day(storage, D2, "greenhouse", {"acme": jobs}, attempt_id="local-c", hour=11)
    bronze, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert bronze.loaded == 1  # only the replaced company-day was reloaded
    assert counts(db) == before
    attempts = query(
        db,
        f"SELECT DISTINCT attempt_id FROM bronze.raw_postings WHERE snapshot_date = '{D2}'",
    )
    assert attempts == [("local-c",)]


def test_replaced_pointer_with_different_content_swaps_rows_not_appends(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(
        storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]}, attempt_id="local-a"
    )
    run_pipeline(storage, db)
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")]}, attempt_id="local-b", hour=11)
    run_pipeline(storage, db)
    rows = query(db, "SELECT external_posting_id, attempt_id FROM bronze.raw_postings")
    assert rows == [("1", "local-b")]


def test_pipeline_twice_leaves_row_counts_unchanged(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("2")]})
    ingest_day(storage, D2, "ashby", {"zeta": [ashby_job("z1")]})
    run_pipeline(storage, db)
    first = counts(db)
    bronze, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert bronze.loaded == 0
    assert bronze.skipped_unchanged == bronze.pointers_seen == 3
    assert counts(db) == first


def test_bronze_follows_pointers_only(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")]}, attempt_id="local-good")
    # A later attempt that fails leaves raw pages behind but must not move the pointer.
    ingest_day(storage, D1, "greenhouse", {"acme": None}, attempt_id="local-bad")
    # A stray raw page that no pointer references is ignored.
    storage.put(
        "raw/source=greenhouse/company=acme/dt=2026-11-10/attempt=local-orphan/page=0001.json.gz",
        b"not even gzip",
        content_type="application/json",
    )
    bronze, _ = run_pipeline(storage, db)
    assert bronze.pointers_seen == 1
    assert not bronze.failed
    assert query(db, "SELECT DISTINCT attempt_id FROM bronze.raw_postings") == [("local-good",)]


def test_bad_snapshot_is_isolated_and_bronze_left_untouched(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")], "beta": [gh_job("b1")]})
    run_pipeline(storage, db)
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("1")], "beta": [gh_job("b1")]})
    page = next(
        k
        for k in storage.list_keys("raw/")
        if "company=acme/dt=2026-11-11" in k and k.endswith(".json.gz")
    )
    storage.put(page, b"corrupted", content_type="application/json")  # hash no longer matches

    bronze, _ = run_pipeline(storage, db)
    assert [f.reason for f in bronze.failed] == ["page_verification_failed"]
    assert bronze.loaded == 1  # beta still loaded
    acme_days = query(
        db, "SELECT snapshot_date FROM bronze.snapshot_pointers WHERE board_id = 'acme'"
    )
    assert [r[0] for r in acme_days] == [D1]  # the bad day was not loaded


def test_cross_source_duplicate_is_flagged_not_merged(storage: LocalStorage, db: Path) -> None:
    ingest_day(
        storage,
        D1,
        "greenhouse",
        {"acme": [gh_job("g1", title="Data Engineer", loc="New York, NY")]},
        companies={"acme": "Acme Inc"},
    )
    ingest_day(
        storage,
        D2,
        "ashby",
        {"acme-ashby": [ashby_job("a1", title="Data  Engineer", loc="new york ny")]},
        companies={"acme-ashby": "Acme Inc"},
    )
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    rows = query(
        db,
        "SELECT source_id, probable_duplicate_of IS NOT NULL, "
        "probable_duplicate_of = (SELECT posting_key FROM silver.dim_postings "
        "WHERE source_id = 'greenhouse') FROM silver.dim_postings ORDER BY 1",
    )
    assert rows == [("ashby", True, True), ("greenhouse", False, None)]
    assert counts(db)["silver.dim_postings"] == 2  # flagged, never merged


def test_same_source_postings_are_never_flagged(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})  # same title/loc
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert query(
        db, "SELECT count(*) FROM silver.dim_postings WHERE probable_duplicate_of IS NOT NULL"
    ) == [(0,)]


def test_cli_warehouse_commands_end_to_end(
    storage: LocalStorage, db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from jobpulse import cli
    from jobpulse.config import load_sources
    from jobpulse.ingest.report import build_report
    from jobpulse.ops.s3_ops import write_ops

    run = ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")]})
    write_ops(storage, run, load_sources(), build_report(run))
    local = ["--local-dir", str(tmp_path / "s3"), "--db", str(db)]

    assert cli.main(["load-ops", *local]) == cli.EXIT_OK
    assert cli.main(["load-bronze", *local]) == cli.EXIT_OK
    assert cli.main(["build-silver", "--db", str(db)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Loaded: **1**" in out
    assert "Failures: **0**" in out
    tasks = query(db, "SELECT task FROM ops.pipeline_runs ORDER BY 1")
    assert tasks == [("bronze",), ("ingest",), ("silver",)]
    assert counts(db)["silver.dim_postings"] == 1
