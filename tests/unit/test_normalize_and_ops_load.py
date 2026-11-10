from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from jobpulse.bronze.normalize import (
    UnknownSourceError,
    extract_jobs,
    html_to_text,
    normalize_posting,
)
from jobpulse.config import load_pipeline, load_sources
from jobpulse.ingest.report import build_report
from jobpulse.ingest.storage import LocalStorage
from jobpulse.ops.load_ops import ensure_ops_tables, load_ops
from jobpulse.warehouse import WarehouseError, connect
from tests.conftest import load_fixture
from tests.unit.test_bronze_silver import D1, D2
from tests.warehouse_helpers import gh_job, ingest_day, warehouse_cfg


@pytest.mark.parametrize(
    ("source", "fixture"),
    [
        ("greenhouse", "greenhouse_jobs.json"),
        ("lever", "lever_postings.json"),
        ("ashby", "ashby_jobs.json"),
    ],
)
def test_real_fixtures_normalize_with_every_field(source: str, fixture: str) -> None:
    jobs = extract_jobs(source, load_fixture(fixture))
    assert jobs
    for job in jobs:
        n = normalize_posting(source, job)
        assert n.external_posting_id == str(job["id"])
        assert n.title
        assert n.apply_url.startswith("https://")
        assert n.description_text
        assert "<" not in n.description_text  # markup stripped
        assert len(n.content_hash) == 64


def test_volatile_fields_do_not_change_content_hash_but_content_does() -> None:
    job = extract_jobs("greenhouse", load_fixture("greenhouse_jobs.json"))[0]
    base = normalize_posting("greenhouse", job).content_hash
    assert normalize_posting("greenhouse", {**job, "updated_at": "2030-01-01"}).content_hash == base
    assert normalize_posting("greenhouse", {**job, "requisition_id": "X"}).content_hash == base
    assert (
        normalize_posting("greenhouse", {**job, "title": job["title"] + "!"}).content_hash != base
    )


def test_greenhouse_double_escaped_html_is_unwrapped() -> None:
    job = {"id": 1, "title": "t", "content": "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;"}
    assert normalize_posting("greenhouse", job).description_text == "Hello & welcome"


def test_html_to_text_is_deterministic_and_handles_none() -> None:
    assert html_to_text("<div><p>a</p><p>b&nbsp;c</p></div>") == "a b c"
    assert html_to_text(None) == ""


def test_unknown_source_is_rejected() -> None:
    with pytest.raises(UnknownSourceError):
        normalize_posting("nope", {"id": 1})


def test_warehouse_requires_a_token_or_a_local_file(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = warehouse_cfg()
    monkeypatch.delenv(cfg.token_env_var, raising=False)
    with pytest.raises(WarehouseError, match=cfg.token_env_var):
        connect(cfg)


def test_local_warehouse_creates_schemas(tmp_path: Path) -> None:
    cfg = warehouse_cfg()
    con = connect(cfg, db_path=tmp_path / "w.duckdb")
    rows = con.execute("select schema_name from information_schema.schemata").fetchall()
    schemas = {r[0] for r in rows}
    assert {cfg.ops_schema, cfg.bronze_schema, cfg.silver_schema} <= schemas


def _write_ops(storage: LocalStorage, day: date, attempt: str, hour: int) -> None:
    from jobpulse.ops.s3_ops import write_ops

    run = ingest_day(
        storage,
        day,
        "greenhouse",
        {"acme": [gh_job("1")], "gone": None},
        attempt_id=attempt,
        hour=hour,
    )
    write_ops(storage, run, load_sources(), build_report(run))


def test_load_ops_is_idempotent_and_keeps_failures(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "s3")
    _write_ops(storage, D1, "local-a", 9)
    _write_ops(storage, D2, "local-b", 9)
    cfg = load_pipeline().warehouse
    con = connect(cfg, db_path=tmp_path / "w.duckdb")

    first = load_ops(storage, con, cfg)
    assert sorted(first.attempts_loaded) == ["local-a", "local-b"]
    assert first.company_snapshots_loaded == 4

    def snapshot() -> tuple[int, int, int]:
        return (
            con.execute("select count(*) from ops.pipeline_runs").fetchone()[0],  # type: ignore[index]
            con.execute("select count(*) from ops.company_snapshots").fetchone()[0],  # type: ignore[index]
            con.execute("select count(*) from ops.source_registry").fetchone()[0],  # type: ignore[index]
        )

    before = snapshot()
    second = load_ops(storage, con, cfg)
    assert second.attempts_loaded == []
    assert second.company_snapshots_loaded == 0
    assert snapshot() == before

    failed = con.execute(
        "select reason_code, status from ops.company_snapshots where board_id = 'gone'"
    ).fetchall()
    assert failed == [("rate_limited", "failed")] * 2
    registry = con.execute("select source_id from ops.source_registry order by 1").fetchall()
    assert registry == [("ashby",), ("greenhouse",), ("lever",)]


def test_ops_tables_are_created_once(tmp_path: Path) -> None:
    cfg = warehouse_cfg()
    con = duckdb.connect(str(tmp_path / "w.duckdb"))
    con.execute(f"create schema {cfg.ops_schema}")
    ensure_ops_tables(con, cfg)
    ensure_ops_tables(con, cfg)  # second call is a no-op
    assert con.execute("select count(*) from ops.pipeline_runs").fetchone() == (0,)
