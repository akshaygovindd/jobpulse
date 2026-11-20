"""Streamlit app: page smoke tests (AppTest) and the read-only / gold-only guarantees.

A fixture warehouse is built by the real pipeline (silver + gold) from synthetic postings, so the
pages run against real gold tables. No network, no MotherDuck.
"""

from __future__ import annotations

import ast
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from jobpulse.app_data import (
    GOLD_TABLES,
    LOCAL_DB_ENV,
    READ_TOKEN_ENV,
    AppDataError,
    connect,
    read_gold,
    validate_statement,
)
from jobpulse.config import load_extraction, load_pipeline
from jobpulse.extract.store import ensure_extract_tables
from jobpulse.ingest.storage import LocalStorage
from jobpulse.ops.load_ops import ensure_ops_tables
from tests.unit.test_gold import add_extraction, gold, seed
from tests.unit.test_gold_visa import lca_case, load_ref
from tests.warehouse_helpers import gh_job

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "streamlit_app.py"
PAGES = [
    "app_pages/market_overview.py",
    "app_pages/hiring_trends.py",
    "app_pages/salary.py",
    "app_pages/visa.py",
    "app_pages/pipeline_health.py",
]
NOW = datetime(2026, 11, 20, 8, tzinfo=UTC)


def _populate(db: Path, tmp_path: Path, *, full: bool) -> None:
    storage = LocalStorage(tmp_path / "s3")
    jobs = [gh_job("1"), gh_job("2", content="two"), gh_job("3", content="three")]
    seed(storage, db, jobs, days=(date(2026, 11, 10), date(2026, 11, 11)))
    if full:
        add_extraction(
            db, "1", skills=["SQL", "dbt"], salary_min=100000.0, salary_max=140000.0,
            salary_currency="USD", salary_period="year", sponsorship_statement="offered",
            sponsorship_original_text="We sponsor visas",
        )  # fmt: skip
        add_extraction(db, "2", skills=["SQL"], role_family="data_scientist")
        load_ref(db, [lca_case(i) for i in range(1, 8)] + [lca_case(9, status="WITHDRAWN")])
        con = duckdb.connect(str(db))
        try:
            ensure_ops_tables(con, load_pipeline().warehouse)
            ensure_extract_tables(con, load_extraction())
            con.execute(
                "INSERT INTO ops.pipeline_runs VALUES ('a1', DATE '2026-11-11', 'ingest', "
                "'greenhouse', ?, ?, 5.0, 'success', 1, 1, 0, 10, 10, NULL, 0.0, 'prod')",
                [NOW, NOW],
            )
            con.execute(
                "INSERT INTO extract.llm_calls VALUES ('c1','a1','h1','m1','sync',100,50,0,"
                "0.01,10,'ok',?)",
                [NOW],
            )
        finally:
            con.close()
    gold(db)
    con = duckdb.connect(str(db))
    con.execute("CHECKPOINT")
    con.close()


def _read_only_copy(db: Path) -> Path:
    """In one process the dbt adapter may still hold a read-write handle on `db`, and DuckDB
    refuses a read-only handle on the same file. The deployed app is its own process, so it does
    not meet this; the tests open a copy instead."""
    out = db.with_name("app_copy.duckdb")
    shutil.copy(db, out)
    return out


@pytest.fixture(scope="module")
def full_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("app_full")
    db = tmp / "wh.duckdb"
    _populate(db, tmp, full=True)
    return _read_only_copy(db)


@pytest.fixture(scope="module")
def bare_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Gold built with no extractions, no LCA data and no ops data."""
    tmp = tmp_path_factory.mktemp("app_bare")
    db = tmp / "wh.duckdb"
    _populate(db, tmp, full=False)
    return _read_only_copy(db)


def _run_pages(db: Path, monkeypatch: pytest.MonkeyPatch) -> list[AppTest]:
    monkeypatch.setenv(LOCAL_DB_ENV, str(db))
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not at.exception, at.exception
    out = [at]
    for page in PAGES[1:]:
        at = AppTest.from_file(str(APP), default_timeout=20).run()
        at.switch_page(page).run()
        assert not at.exception, (page, at.exception)
        out.append(at)
    return out


def _text(at: AppTest) -> str:
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption]
    parts += (
        [e.value for e in at.info] + [e.value for e in at.warning] + [e.value for e in at.error]
    )
    parts += [e.value for e in at.success]
    return "\n".join(str(p) for p in parts)


def test_every_page_renders_with_data(full_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = _run_pages(full_db, monkeypatch)
    assert len(pages) == len(PAGES)


def test_every_page_renders_on_a_warehouse_with_no_visa_ops_or_extraction_data(
    bare_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_pages(bare_db, monkeypatch)


def test_visa_page_shows_disclaimer_counts_and_quotes(
    full_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LOCAL_DB_ENV, str(full_db))
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    at.switch_page("app_pages/visa.py").run()
    assert not at.exception
    text = _text(at)
    assert "not a promise of sponsorship" in text
    assert "We sponsor visas" in text  # the grounded quote is shown
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Certified filings"] == "7"  # n_certified always beside the class
    assert metrics["Withdrawn or denied"] == "1"


def test_salary_page_keeps_the_two_datasets_apart(
    full_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LOCAL_DB_ENV, str(full_db))
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    at.switch_page("app_pages/salary.py").run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Advertised salary", "LCA offered wages"]
    assert "never blended" in _text(at)
    assert "Postings with an advertised salary" in {m.label for m in at.metric}


def test_pipeline_health_flags_stale_data(full_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # the fixture snapshots are from 2026-11, so against today's clock they are far older than 30 h
    monkeypatch.setenv(LOCAL_DB_ENV, str(full_db))
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    at.switch_page("app_pages/pipeline_health.py").run()
    assert not at.exception
    assert any("Stale data" in str(e.value) for e in at.error)


def test_missing_data_source_shows_a_message_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(LOCAL_DB_ENV, raising=False)
    monkeypatch.delenv(READ_TOKEN_ENV, raising=False)
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not at.exception
    assert any(READ_TOKEN_ENV in str(e.value) for e in at.error)


# ---- read-only and gold-only guarantees ------------------------------------------------------


def test_connection_is_read_only(full_db: Path) -> None:
    con = connect({LOCAL_DB_ENV: str(full_db)})
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE gold.x (a INT)")
    assert len(read_gold(con, "SELECT count(*) AS n FROM gold.fct_postings")) == 1


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM gold.fct_postings",
        "SELECT 1; DROP TABLE gold.fct_postings",
        "SELECT * FROM silver.dim_postings",
        "SELECT * FROM gold_stage.int_lca_certified",
        "SELECT * FROM gold.not_a_table",
        "SELECT * FROM ref.lca_disclosures",
        "SELECT * FROM extract.extractions",
        "SELECT * FROM bronze.raw_postings",
        "SELECT * FROM fct_postings",
        "SELECT * FROM read_csv('/etc/passwd')",
        "ATTACH 'x.db'",
        "PRAGMA database_size",
        "SELECT * FROM gold.fct_postings -- hi",
        "SELECT * FROM gold.fct_postings a JOIN ops.pipeline_runs b ON true",
        "",
    ],
)
def test_statements_outside_gold_select_are_refused(sql: str) -> None:
    with pytest.raises(AppDataError):
        validate_statement(sql)


def test_allowed_statements_pass() -> None:
    validate_statement("SELECT a FROM gold.fct_postings WHERE title = 'drop table x'")
    validate_statement(
        "WITH t AS (SELECT * FROM gold.dim_company) "
        "SELECT * FROM t JOIN gold.agg_data_quality USING (x)"
    )


def test_app_code_reads_gold_tables_only() -> None:
    """Every table named in the app's SQL strings is a gold table in the allow-list, and no page
    imports duckdb or opens a connection itself."""
    files = [*sorted((ROOT / "app").rglob("*.py")), ROOT / "src/jobpulse/app_ui.py"]
    seen: set[str] = set()
    for f in files:
        tree = ast.parse(f.read_text())
        imported = {
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(tree)
            for a in (n.names if isinstance(n, ast.Import | ast.ImportFrom) else [])
        } | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        if f.parent.name == "app_pages":
            assert "duckdb" not in imported, f.name
        strings = " ".join(
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        for ref in re.findall(r"(?i)\b(?:from|join)\s+([a-z_]+\.[a-z_]+)", strings):
            schema, table = ref.split(".")
            assert schema == "gold", (f.name, ref)
            assert table in GOLD_TABLES, (f.name, ref)
            seen.add(table)
    assert seen  # the pages do query gold
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "CREATE ", "ATTACH "):
        assert not any(forbidden in f.read_text() for f in files if f.name != "app_data.py")


def test_pages_show_human_labels_not_codes(full_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codes = ("ml_ai_engineer", "data_scientist", "HISTORICAL_EVIDENCE", "not_offered", "n_active")
    for at in _run_pages(full_db, monkeypatch):
        visible = _text(at) + " ".join(str(m.label) + str(m.value) for m in at.metric)
        visible += " ".join(str(s.label) for s in at.selectbox)
        for code in codes:
            assert code not in visible, code


def test_market_overview_explains_what_the_ai_reads(
    full_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LOCAL_DB_ENV, str(full_db))
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not at.exception
    assert "43 tech employers" in _text(at)
    assert "Data & AI roles analyzed" in {m.label for m in at.metric}
    assert "only data and AI roles" in _text(at)


def test_visa_page_does_not_show_zero_as_no_sponsorship(
    bare_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LOCAL_DB_ENV, str(bare_db))
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    at.switch_page("app_pages/visa.py").run()
    assert not at.exception
    assert any("Not stated" in str(i.value) for i in at.info)  # nothing analyzed: say so
