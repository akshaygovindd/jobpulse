"""Gold pipeline-health tables: freshness, runs, ingest status, data quality, LLM cost."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from jobpulse.config import load_extraction, load_pipeline
from jobpulse.extract.store import ensure_extract_tables
from jobpulse.ingest.storage import LocalStorage
from jobpulse.ops.load_ops import ensure_ops_tables
from tests.unit.test_gold import add_extraction, gold, seed
from tests.warehouse_helpers import gh_job, query

NOW = datetime(2026, 11, 20, 8, tzinfo=UTC)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "s3")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "wh.duckdb"


def test_health_tables_build_empty_without_ops_data(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    gold(db)
    assert query(db, "SELECT count(*) FROM gold.fct_pipeline_runs") == [(0,)]
    assert query(db, "SELECT count(*) FROM gold.agg_llm_cost_daily") == [(0,)]
    assert query(db, "SELECT count(*) FROM gold.agg_ingest_status_daily") == [(0,)]
    rows = query(db, "SELECT source_id, board_id, max_age_hours FROM gold.fct_snapshot_freshness")
    assert rows == [("greenhouse", "acme", 30)]


def test_health_tables_summarise_ops_and_llm_calls(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1"), gh_job("2", content="other")])
    add_extraction(db, "1", salary_min=100000.0, salary_max=120000.0, salary_currency="USD",
                   salary_period="year", sponsorship_statement="offered",
                   sponsorship_original_text="We sponsor")  # fmt: skip
    con = duckdb.connect(str(db))
    try:
        wh = load_pipeline().warehouse
        ensure_ops_tables(con, wh)
        ensure_extract_tables(con, load_extraction())
        con.execute(
            "INSERT INTO ops.pipeline_runs VALUES "
            "('a1', DATE '2026-11-10', 'ingest', 'greenhouse', ?, ?, 5.0, 'success', 1, 1, 0, "
            "10, 10, NULL, 0.0, 'prod')",
            [NOW, NOW],
        )
        con.execute(
            "INSERT INTO ops.company_snapshots VALUES "
            "('a1', DATE '2026-11-10', 'greenhouse', 'acme', 'Acme', 'complete', NULL, NULL, 1, "
            "2, 'k', true, 'set', ?, ?)",
            [NOW, NOW],
        )
        con.execute(
            "INSERT INTO ops.company_snapshots VALUES "
            "('a2', DATE '2026-11-10', 'greenhouse', 'bad', 'Bad', 'failed', 'http_5xx', NULL, 0, "
            "0, NULL, false, 'none', ?, ?)",
            [NOW, NOW],
        )
        for i, (model, cost, outcome) in enumerate(
            [("m1", 0.01, "ok"), ("m1", 0.02, "ok"), ("m2", 0.05, "escalated")]
        ):
            con.execute(
                "INSERT INTO extract.llm_calls VALUES (?,?,?,?, 'sync', 100, 50, 0, ?, 10, ?, ?)",
                [f"c{i}", "a1", f"h{i}", model, cost, outcome, NOW],
            )
    finally:
        con.close()
    gold(db)
    assert query(
        db, "SELECT model, outcome, n_calls, round(cost_usd, 2) FROM gold.agg_llm_cost_daily "
        "ORDER BY model"
    ) == [("m1", "ok", 2, 0.03), ("m2", "escalated", 1, 0.05)]  # fmt: skip
    assert query(
        db, "SELECT status, reason_code, n_boards FROM gold.agg_ingest_status_daily ORDER BY 1"
    ) == [("complete", "none", 1), ("failed", "http_5xx", 1)]
    assert query(db, "SELECT task, status, rows_kept FROM gold.fct_pipeline_runs") == [
        ("ingest", "success", 10)
    ]
    quality = {
        r[0]: (r[1], r[2])
        for r in query(db, "SELECT metric, numerator, denominator FROM gold.agg_data_quality")
    }
    assert quality["extracted_share"] == (1, 2)
    assert quality["advertised_salary"] == (1, 1)
    assert quality["sponsorship_stated"] == (1, 1)
