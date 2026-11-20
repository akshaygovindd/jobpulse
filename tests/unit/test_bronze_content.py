"""Bronze storage: description/payload are kept once per (posting, content_hash), not daily."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from jobpulse.bronze.loader import ensure_bronze_tables, load_bronze
from jobpulse.ingest.storage import LocalStorage
from tests.warehouse_helpers import (
    TickingClock,
    gh_job,
    ingest_day,
    query,
    run_pipeline,
    warehouse_cfg,
)

D1, D2, D3, D4 = (date(2026, 11, d) for d in (10, 11, 12, 13))


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "s3")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "wh.duckdb"


def test_unchanged_postings_store_their_content_once(storage: LocalStorage, db: Path) -> None:
    for day in (D1, D2, D3):
        ingest_day(storage, day, "greenhouse", {"acme": [gh_job("1"), gh_job("2")]})
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    assert query(db, "SELECT count(*) FROM bronze.raw_postings") == [(6,)]  # one row per day each
    assert query(db, "SELECT count(*) FROM bronze.posting_content") == [(2,)]  # content once
    cols = {
        r[0]
        for r in query(
            db,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'bronze' AND table_name = 'raw_postings'",
        )
    }
    assert not {"payload", "description_text"} & cols
    # silver still sees the description on every observation
    assert query(db, "SELECT count(*) FROM silver.fct_posting_observations") == [(6,)]
    assert query(db, "SELECT description_text FROM silver.dim_postings LIMIT 1") == [
        ("Build pipelines",)
    ]


def test_changed_content_adds_one_content_row_and_a_version(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("1")]})
    ingest_day(storage, D3, "greenhouse", {"acme": [gh_job("1", content="New text")]})
    ingest_day(storage, D4, "greenhouse", {"acme": [gh_job("1")]})  # back to the first content
    _, silver = run_pipeline(storage, db)
    assert silver.success, silver.failures
    # A -> B -> A: two distinct contents stored, not three, and none lost.
    assert query(db, "SELECT count(*) FROM bronze.posting_content") == [(2,)]
    versions = query(
        db, "SELECT valid_from, description_text FROM silver.dim_posting_versions ORDER BY 1"
    )
    assert [v[1] for v in versions] == ["Build pipelines", "New text", "Build pipelines"]
    assert query(db, "SELECT description_text FROM silver.dim_postings") == [("Build pipelines",)]


def test_payload_is_kept_for_the_first_observation_of_each_content(
    storage: LocalStorage, db: Path
) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")]})
    ingest_day(storage, D2, "greenhouse", {"acme": [gh_job("1")]})
    run_pipeline(storage, db)
    rows = query(db, "SELECT first_snapshot_date, json_extract_string(payload, '$.id') "
                     "FROM bronze.posting_content")  # fmt: skip
    assert rows == [(D1, "1")]


def test_replaced_attempt_does_not_leave_orphan_content(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1", content="first")]}, hour=9)
    run_pipeline(storage, db)
    assert query(db, "SELECT count(*) FROM bronze.posting_content") == [(1,)]
    # a later attempt on the same date changes the content: the pointer moves, the old content goes
    ingest_day(
        storage, D1, "greenhouse", {"acme": [gh_job("1", content="second")]}, hour=11,
        attempt_id="local-second",
    )  # fmt: skip
    run_pipeline(storage, db)
    assert query(db, "SELECT description_text FROM bronze.posting_content") == [("second",)]
    assert query(db, "SELECT count(*) FROM bronze.raw_postings") == [(1,)]


def _make_old_shape(db: Path) -> None:
    """The bronze table as it was before content-addressing (payload/description on every row)."""
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA bronze")
    con.execute(
        """CREATE TABLE bronze.raw_postings (
        source_id VARCHAR NOT NULL, board_id VARCHAR NOT NULL, snapshot_date DATE NOT NULL,
        attempt_id VARCHAR NOT NULL, external_posting_id VARCHAR NOT NULL, title VARCHAR,
        location_raw VARCHAR, description_text VARCHAR, apply_url VARCHAR,
        content_hash VARCHAR NOT NULL, payload JSON, object_key VARCHAR NOT NULL,
        loaded_at TIMESTAMPTZ NOT NULL)"""
    )
    loaded = datetime(2026, 11, 12, tzinfo=UTC)
    for d, text, h in (
        (D1, "A", "h1"), (D2, "A", "h1"), (D3, "B", "h2"), (D4, "A", "h1"),
    ):  # fmt: skip
        con.execute(
            "INSERT INTO bronze.raw_postings VALUES "
            "('greenhouse','acme',?, 'att','1','T','NYC',?, 'u', ?, ?::JSON, 'k', ?)",
            [d, text, h, f'{{"d": "{d}"}}', loaded],
        )
    con.close()


def test_migration_from_old_shape_is_lossless_and_idempotent(db: Path) -> None:
    _make_old_shape(db)
    cfg = warehouse_cfg()
    con = duckdb.connect(str(db))
    ensure_bronze_tables(con, cfg)
    ensure_bronze_tables(con, cfg)  # idempotent: second call is a no-op
    con.close()
    assert query(db, "SELECT count(*) FROM bronze.raw_postings") == [(4,)]
    content = query(
        db,
        "SELECT content_hash, first_snapshot_date, description_text, "
        "json_extract_string(payload, '$.d') FROM bronze.posting_content ORDER BY 1",
    )
    # earliest observation of each content wins
    assert content == [("h1", D1, "A", "2026-11-10"), ("h2", D3, "B", "2026-11-12")]
    cols = {r[0] for r in query(db, "SELECT column_name FROM information_schema.columns "
                                    "WHERE table_name = 'raw_postings'")}  # fmt: skip
    assert not {"payload", "description_text"} & cols


def test_rerun_after_migration_loads_nothing_new(storage: LocalStorage, db: Path) -> None:
    ingest_day(storage, D1, "greenhouse", {"acme": [gh_job("1")]})
    run_pipeline(storage, db)
    con = duckdb.connect(str(db))
    result = load_bronze(
        storage, con, warehouse_cfg(), clock=TickingClock(datetime(2026, 12, 2, tzinfo=UTC)),
        attempt_id="again",
    )  # fmt: skip
    con.close()
    assert result.loaded == 0
    assert result.skipped_unchanged == 1
