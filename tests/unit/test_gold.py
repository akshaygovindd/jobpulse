"""P4 fixture tests: the gold dbt layer (models, tests, snapshot) built on a local DuckDB file.

Silver is built from synthetic postings; `extract.extractions` rows are written by hand, so no
network or LLM is involved. CI runs this in the normal pytest step.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from jobpulse.config import load_extraction, load_pipeline
from jobpulse.extract.store import ensure_extract_tables
from jobpulse.gold import build_gold
from jobpulse.ingest.storage import LocalStorage
from tests.warehouse_helpers import gh_job, ingest_day, query, run_pipeline

D1, D2, D3 = date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)  # all in week of Nov 9


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "s3")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "wh.duckdb"


def content_hash(db: Path, external_id: str) -> tuple[str, str]:
    rows = query(
        db,
        "SELECT content_hash, posting_key FROM silver.dim_postings "
        f"WHERE external_posting_id = '{external_id}'",
    )
    assert len(rows) == 1
    return rows[0][0], rows[0][1]


def add_extraction(db: Path, external_id: str, **fields: Any) -> None:
    """Insert one extraction for the posting's current content."""
    ch, pk = content_hash(db, external_id)
    row: dict[str, Any] = {
        "content_hash": ch,
        "schema_version": 1,
        "prompt_version": "test",
        "posting_key": pk,
        "model": "fake-model",
        "escalated": False,
        "skills": [],
        "seniority": "senior",
        "role_family": "data_engineer",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "remote_policy": "onsite",
        "sponsorship_statement": "unspecified",
        "confidence": 0.9,
        "extracted_at": datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
        "salary_period_inferred": False,
    }
    row.update(fields)
    con = duckdb.connect(str(db))
    try:
        ensure_extract_tables(con, load_extraction())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        con.execute(
            f"INSERT INTO extract.extractions ({cols}) VALUES ({marks})", list(row.values())
        )
    finally:
        con.close()


def gold(db: Path) -> None:
    result, _maps = build_gold(load_pipeline(), db_path=db)
    assert result.success, result.failures
    assert result.tests_passed > 0


def fct(db: Path, external_id: str) -> dict[str, Any]:
    con = duckdb.connect(str(db))
    try:
        cur = con.execute(
            "SELECT f.* FROM gold.fct_postings f JOIN silver.dim_postings d USING (posting_key) "
            "WHERE d.external_posting_id = ?",
            [external_id],
        )
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    return dict(zip(names, rows[0], strict=True))


def seed(
    storage: LocalStorage, db: Path, jobs: Sequence[dict[str, Any]], days: Sequence[date] = (D1,)
) -> None:
    for d in days:
        ingest_day(storage, d, "greenhouse", {"acme": list(jobs)})
    bronze, silver = run_pipeline(storage, db)
    assert not bronze.failed
    assert silver.success, silver.failures


def test_silver_builds_without_extract_schema(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    assert query(
        db, "SELECT count(*) FROM information_schema.schemata WHERE schema_name='extract'"
    ) == [(0,)]


def test_gold_builds_with_no_extractions(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1"), gh_job("2")])
    gold(db)
    assert query(
        db, "SELECT count(*), count(*) FILTER (WHERE has_extraction) FROM gold.fct_postings"
    ) == [(2, 0)]
    assert query(db, "SELECT count(*) FROM gold.agg_salary_by_role_city") == [(0,)]
    assert query(db, "SELECT count(*) FROM gold.agg_skill_demand_weekly") == [(0,)]
    assert query(
        db, "SELECT company_id, n_postings, n_active, n_extracted FROM gold.dim_company"
    ) == [("acme", 2, 2, 0)]


def test_annualization_only_where_period_known(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job(str(i), loc="NYC") for i in range(1, 7)])
    add_extraction(
        db,
        "1",
        salary_min=120000.0,
        salary_max=150000.0,
        salary_currency="USD",
        salary_period="year",
    )
    add_extraction(
        db,
        "2",
        salary_min=50.0,
        salary_max=60.0,
        salary_currency="USD",
        salary_period="hour",
        salary_period_inferred=True,
    )
    add_extraction(
        db, "3", salary_min=10000.0, salary_max=None, salary_currency="EUR", salary_period="month"
    )
    add_extraction(
        db, "4", salary_min=500.0, salary_max=500.0, salary_currency="USD", salary_period="day"
    )
    add_extraction(
        db, "5", salary_min=90000.0, salary_max=None, salary_currency="USD", salary_period=None
    )
    add_extraction(db, "6")
    gold(db)
    f1, f2, f3, f4, f5, f6 = (fct(db, str(i)) for i in range(1, 7))
    assert (f1["salary_min_annual"], f1["salary_max_annual"]) == (120000.0, 150000.0)
    assert (f2["salary_min_annual"], f2["salary_max_annual"]) == (50.0 * 2080, 60.0 * 2080)
    assert f2["salary_period_inferred"] is True
    assert f1["salary_period_inferred"] is False
    assert f3["salary_min_annual"] == 120000.0
    assert f3["salary_max_annual"] is None
    # day is not annualized, and a missing period is never guessed in gold
    assert f4["salary_min_annual"] is None
    assert f4["has_advertised_salary"] is True
    assert f5["salary_min_annual"] is None
    assert f5["has_advertised_salary"] is True
    assert f6["has_advertised_salary"] is False
    assert f1["city"] == "New York, New York"
    assert (f1["city_name"], f1["city_state"], f1["city_country"]) == (
        "New York",
        "New York",
        "United States",
    )


def test_salary_aggregate_coverage_and_no_currency_blend(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job(str(i), loc="Boston, MA") for i in range(1, 10)])
    for i in range(1, 6):  # five USD yearly salaries: enough for percentiles
        add_extraction(
            db,
            str(i),
            salary_min=100000.0 + 10000 * i,
            salary_max=100000.0 + 10000 * i,
            salary_currency="USD",
            salary_period="year",
        )
    add_extraction(
        db, "6", salary_min=70000.0, salary_max=80000.0, salary_currency="GBP", salary_period="year"
    )
    add_extraction(db, "7")
    add_extraction(db, "8")
    add_extraction(db, "9")
    gold(db)
    rows = {
        r[0]: r[1:]
        for r in query(
            db,
            "SELECT salary_currency, n_postings, n_with_salary, round(coverage_pct, 2), "
            "n_currency_salary, n_annualized, p25_annual, p50_annual, p75_annual "
            "FROM gold.agg_salary_by_role_city WHERE city = 'Boston, Massachusetts'",
        )
    }
    assert set(rows) == {"USD", "GBP"}  # never blended
    usd, gbp = rows["USD"], rows["GBP"]
    assert usd[:5] == (9, 6, 66.67, 5, 5)  # group-level coverage is repeated on each currency row
    assert (usd[5], usd[6], usd[7]) == (120000.0, 130000.0, 140000.0)
    assert gbp[:5] == (9, 6, 66.67, 1, 1)
    assert gbp[5:] == (None, None, None)  # below min_salaries_for_percentiles


def test_same_named_cities_in_different_places_stay_separate(
    storage: LocalStorage, db: Path
) -> None:
    locs = [
        "Dublin, Ireland",
        "Dublin, CA",
        "US-CA-Dublin",
        "Dublin",
        "Redwood CIty, CA",
        "Redwood City, CA",
        "Redwood City, CA",
    ]
    seed(storage, db, [gh_job(str(i), loc=loc) for i, loc in enumerate(locs, start=1)])
    for i in range(1, len(locs) + 1):
        add_extraction(db, str(i))
    gold(db)
    cities = dict(
        query(db, "SELECT city, count(*) FROM gold.fct_postings GROUP BY 1")  # type: ignore[arg-type]
    )
    assert cities == {
        "Dublin, Ireland": 2,
        "Dublin, California": 2,
        "Redwood City, California": 3,
    }
    groups = query(db, "SELECT DISTINCT city FROM gold.agg_salary_by_role_city ORDER BY city")
    assert [g[0] for g in groups] == [
        "Dublin, California",
        "Dublin, Ireland",
        "Redwood City, California",
    ]


def test_skills_are_normalized_and_deduplicated(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1"), gh_job("2")])
    add_extraction(db, "1", skills=["Apache Spark", "spark", "SQL", "Data Pipelines"])
    add_extraction(db, "2", skills=["sql", "data pipeline"])
    gold(db)
    assert query(db, "SELECT canonical_name, n_spellings FROM gold.dim_skill ORDER BY 1") == [
        ("data pipeline", 2),
        ("spark", 2),
        ("sql", 2),
    ]
    assert query(
        db,
        "SELECT d.external_posting_id, s.canonical_name FROM gold.bridge_posting_skill b "
        "JOIN silver.dim_postings d USING (posting_key) JOIN gold.dim_skill s USING (skill_id) "
        "ORDER BY 1, 2",
    ) == [
        ("1", "data pipeline"),
        ("1", "spark"),
        ("1", "sql"),
        ("2", "data pipeline"),
        ("2", "sql"),
    ]


def test_weekly_skill_demand(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1"), gh_job("2")], days=(D1, D2))
    add_extraction(db, "1", skills=["SQL", "Spark"])
    add_extraction(db, "2", skills=["SQL"])
    gold(db)
    rows = query(
        db,
        "SELECT week_start, s.canonical_name, active_postings, new_postings, share_of_postings "
        "FROM gold.agg_skill_demand_weekly a JOIN gold.dim_skill s USING (skill_id) ORDER BY 2",
    )
    assert rows == [
        (date(2026, 11, 9), "spark", 1, 1, 0.5),
        (date(2026, 11, 9), "sql", 2, 2, 1.0),
    ]


def test_rerun_is_idempotent_and_snapshot_tracks_changes(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1"), gh_job("2")], days=(D1,))
    add_extraction(db, "1", skills=["SQL"])
    gold(db)
    tables = [
        "gold.fct_postings",
        "gold.dim_company",
        "gold.dim_skill",
        "gold.bridge_posting_skill",
        "gold.agg_skill_demand_weekly",
        "gold.agg_salary_by_role_city",
        "gold.snap_postings",
    ]

    def count_all() -> list[int]:
        return [int(query(db, f"SELECT count(*) FROM {t}")[0][0]) for t in tables]

    first = count_all()
    gold(db)
    assert count_all() == first
    assert query(db, "SELECT count(*) FROM gold.snap_postings") == [(2,)]

    # posting 2's content changes: a new snapshot version; posting 1 is untouched
    ingest_day(
        storage, D2, "greenhouse", {"acme": [gh_job("1"), gh_job("2", content="Different text")]}
    )
    run_pipeline(storage, db)
    gold(db)
    assert query(db, "SELECT count(*) FROM gold.snap_postings WHERE dbt_valid_to IS NULL") == [(2,)]
    versions = dict(
        query(
            db,
            "SELECT d.external_posting_id, count(*) FROM gold.snap_postings s "
            "JOIN silver.dim_postings d USING (posting_key) GROUP BY 1",
        )
    )
    assert versions == {"1": 1, "2": 2}
    # the changed posting has no extraction for its new content until extract runs again
    assert fct(db, "2")["has_extraction"] is False
    assert fct(db, "1")["has_extraction"] is True


def test_latest_extraction_wins_per_content_hash(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    add_extraction(
        db,
        "1",
        prompt_version="old",
        seniority="junior",
        extracted_at=datetime(2026, 11, 10, tzinfo=UTC),
    )
    add_extraction(
        db,
        "1",
        prompt_version="new",
        seniority="senior",
        extracted_at=datetime(2026, 11, 11, tzinfo=UTC),
    )
    gold(db)
    assert fct(db, "1")["seniority"] == "senior"
    assert query(db, "SELECT count(*) FROM gold.fct_postings") == [(1,)]


def test_cli_build_gold_records_a_pipeline_run(storage: LocalStorage, db: Path) -> None:
    from jobpulse.cli import main

    seed(storage, db, [gh_job("1")])
    assert main(["build-gold", "--db", str(db)]) == 0
    rows = query(db, "SELECT task, status, rows_kept FROM ops.pipeline_runs WHERE task = 'gold'")
    assert rows == [("gold", "success", 1)]


def _gold_report(args: list[str]) -> int:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "gold_report.py"
    spec = importlib.util.spec_from_file_location("gold_report", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main(args))


def test_gold_report_without_gold_schema_does_not_crash(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    duckdb.connect(str(db)).close()  # an empty warehouse: no gold schema at all
    assert _gold_report(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "does not exist yet" in out
    assert "database_size" in out


def test_gold_report_on_built_gold(
    storage: LocalStorage, db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(storage, db, [gh_job("1", loc="Boston, MA")])
    add_extraction(db, "1")
    gold(db)
    assert _gold_report(["--db", str(db)]) == 0
    assert "fct_postings rows: 1" in capsys.readouterr().out


def test_bulk_insert_chunks_and_handles_empty() -> None:
    from jobpulse.gold import bulk_insert

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (a VARCHAR, b INTEGER)")
    bulk_insert(con, "t", [])
    assert con.execute("SELECT count(*) FROM t").fetchone() == (0,)
    rows = [(f"v{i}", i) for i in range(1203)]  # 3 chunks of 500, 500, 203
    bulk_insert(con, "t", rows)
    assert con.execute("SELECT count(*), sum(b), count(DISTINCT a) FROM t").fetchone() == (
        1203,
        sum(range(1203)),
        1203,
    )
    bulk_insert(con, "t", [("só", 1), (None, 2)])  # unicode and NULL pass through as parameters
    assert con.execute("SELECT count(*) FROM t WHERE a IS NULL OR a = 'só'").fetchone() == (2,)


def _storage_script() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "storage_projection.py"
    spec = importlib.util.spec_from_file_location("storage_projection", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_storage_projection_on_a_local_file_and_md_view_absent(
    storage: LocalStorage, db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _storage_script()
    seed(storage, db, [gh_job("1")])
    con = duckdb.connect(str(db))
    assert module.md_storage(con, "jobpulse") is None  # no md_information_schema locally
    con.close()
    assert module.main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "used:" in out
    assert "posting_content rows: 1" in out
    assert module.main(["--marginal", "1"]) == 2  # --marginal needs a local file


def test_salary_by_role_has_no_city_dimension_and_matches_city_totals(
    storage: LocalStorage, db: Path
) -> None:
    seed(storage, db, [gh_job(str(i), loc="NYC") for i in range(1, 7)])
    for i in range(1, 7):
        add_extraction(
            db, str(i), salary_min=100000.0 + i, salary_max=120000.0, salary_currency="USD",
            salary_period="year",
        )  # fmt: skip
    gold(db)
    by_role = query(
        db,
        "SELECT role_family, seniority, salary_currency, n_postings, n_annualized, "
        "p50_annual IS NOT NULL FROM gold.agg_salary_by_role",
    )
    assert by_role == [("data_engineer", "senior", "USD", 6, 6, True)]
    by_city = query(db, "SELECT sum(n_annualized) FROM gold.agg_salary_by_role_city")
    assert by_city == [(6,)]


def test_apply_url_is_published_only_for_https_on_allow_listed_hosts(
    storage: LocalStorage, db: Path
) -> None:
    urls = {
        "1": "https://boards.greenhouse.io/acme/jobs/1",
        "2": "https://jobs.lever.co/acme/abc",
        "3": "http://boards.greenhouse.io/acme/jobs/3",  # not https
        "4": "https://evil.example/acme/jobs/4",  # host not allow-listed
        "5": "https://boards.greenhouse.io@evil.example/x",  # credentials trick
        "6": "https://boards.greenhouse.io:8443/acme/6",  # port
        "7": "https://jobs.ashbyhq.com/acme/7 onclick",  # whitespace
        "8": "javascript:alert(1)",
    }
    jobs = []
    for pid, url in urls.items():
        job = gh_job(pid)
        job["absolute_url"] = url
        jobs.append(job)
    seed(storage, db, jobs)
    gold(db)
    got = {pid: fct(db, pid)["apply_url"] for pid in urls}
    assert got["1"] == urls["1"]
    assert got["2"] == urls["2"]
    assert all(got[p] is None for p in "345678"), got
