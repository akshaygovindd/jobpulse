"""Publishing the gold layer for the public app: export validation, atomic replace, and the app's
token-free published-file mode."""

from __future__ import annotations

import shutil
import tempfile
from datetime import date
from pathlib import Path

import duckdb
import httpx
import pytest

from jobpulse.app_data import (
    GOLD_TABLES,
    GOLD_URL_ENV,
    LOCAL_DB_ENV,
    READ_TOKEN_ENV,
    AppDataError,
    connect,
    connect_published,
    read_gold,
)
from jobpulse.config import PublishConfig, load_publish
from jobpulse.ingest.storage import LocalStorage
from jobpulse.publish import PublishError, export_gold
from tests.unit.test_app import PAGES, _run_pages
from tests.unit.test_gold import add_extraction, gold, seed
from tests.warehouse_helpers import gh_job

URL = load_publish().release.url_prefix + "gold-latest/jobpulse_gold.duckdb"
CFG = load_publish().model_copy(
    update={
        "export": load_publish().export.model_copy(
            update={"min_rows": {"fct_postings": 2, "dim_skill": 1}}
        )
    }
)


@pytest.fixture(scope="module")
def source_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("publish_src")
    db = tmp / "wh.duckdb"
    seed(
        LocalStorage(tmp / "s3"),
        db,
        [gh_job("1"), gh_job("2", content="two"), gh_job("3", content="three")],
        days=(date(2026, 11, 10), date(2026, 11, 11)),
    )
    add_extraction(db, "1", skills=["Python", "SQL"])
    gold(db)
    con = duckdb.connect(str(db))
    con.execute("CHECKPOINT")
    con.close()
    out = tmp / "copy.duckdb"
    shutil.copy(db, out)
    return out


@pytest.fixture
def source(source_db: Path, tmp_path: Path) -> duckdb.DuckDBPyConnection:
    copy = tmp_path / "src.duckdb"
    shutil.copy(source_db, copy)
    con = duckdb.connect(str(copy))
    yield con  # type: ignore[misc]
    con.close()


def test_export_holds_exactly_the_allow_listed_gold_tables(
    source: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    out = tmp_path / "published.duckdb"
    counts = export_gold(source, out, CFG)
    assert set(counts) == GOLD_TABLES
    con = duckdb.connect(str(out), read_only=True)
    try:
        tables = con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
        ).fetchall()
        assert {(s, t) for s, t in tables} == {("gold", t) for t in GOLD_TABLES}
        for table, n in counts.items():
            assert con.execute(f"SELECT count(*) FROM gold.{table}").fetchone() == (n,)
        assert counts["fct_postings"] >= 3
    finally:
        con.close()
    assert not out.with_name(out.name + ".tmp").exists()


def test_no_working_or_raw_data_reaches_the_export(
    source: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    out = tmp_path / "published.duckdb"
    export_gold(source, out, CFG)
    con = duckdb.connect(str(out), read_only=True)
    try:
        schemas = {r[0] for r in con.execute("SELECT schema_name FROM duckdb_schemas()").fetchall()}
    finally:
        con.close()
    assert not schemas & {"bronze", "silver", "ops", "extract", "ref", "gold_stage"}
    assert b"Build pipelines" not in out.read_bytes()  # the posting text is not in gold


def test_a_too_small_export_is_refused_and_the_previous_file_stays(
    source: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    out = tmp_path / "published.duckdb"
    out.write_bytes(b"yesterday")
    strict = CFG.model_copy(
        update={"export": CFG.export.model_copy(update={"min_rows": {"fct_postings": 1_000_000}})}
    )
    with pytest.raises(PublishError, match="below"):
        export_gold(source, out, strict)
    assert out.read_bytes() == b"yesterday"
    assert not out.with_name(out.name + ".tmp").exists()


def test_a_missing_gold_table_is_refused_and_the_previous_file_stays(
    source: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    source.execute("DROP TABLE gold.dim_skill")
    out = tmp_path / "published.duckdb"
    out.write_bytes(b"yesterday")
    with pytest.raises(PublishError, match="dim_skill"):
        export_gold(source, out, CFG)
    assert out.read_bytes() == b"yesterday"


def test_a_successful_export_replaces_the_previous_file(
    source: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    out = tmp_path / "published.duckdb"
    out.write_bytes(b"yesterday")
    export_gold(source, out, CFG)
    assert out.read_bytes() != b"yesterday"
    duckdb.connect(str(out), read_only=True).close()


# ---- the app side: downloading the published file, no token ----------------------------------


class Server:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status, self.calls = body, status, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self.status, content=self.body if self.status == 200 else b"")


@pytest.fixture
def exported(source: duckdb.DuckDBPyConnection, tmp_path: Path) -> bytes:
    out = tmp_path / "published.duckdb"
    export_gold(source, out, CFG)
    return out.read_bytes()


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    d = tmp_path / "tmpdir"
    d.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(d))
    return d


def test_published_file_is_downloaded_read_only_and_queryable(exported: bytes, cache: Path) -> None:
    server = Server(exported)
    con = connect_published(URL, cfg=CFG, transport=httpx.MockTransport(server))
    frame = read_gold(con, "SELECT count(*) AS n FROM gold.fct_postings")
    assert int(frame["n"].iloc[0]) >= 3
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE gold.x (a int)")
    con.close()
    assert server.calls == 1


def test_a_fresh_local_copy_is_not_downloaded_again(exported: bytes, cache: Path) -> None:
    server = Server(exported)
    connect_published(URL, cfg=CFG, transport=httpx.MockTransport(server)).close()
    connect_published(URL, cfg=CFG, transport=httpx.MockTransport(server)).close()
    assert server.calls == 1


def test_a_stale_copy_is_refreshed_and_kept_when_the_download_fails(
    exported: bytes, cache: Path
) -> None:
    connect_published(URL, cfg=CFG, transport=httpx.MockTransport(Server(exported))).close()
    later = lambda: 10**12  # noqa: E731
    down = Server(b"", status=404)
    con = connect_published(URL, cfg=CFG, transport=httpx.MockTransport(down), clock=later)
    assert read_gold(con, "SELECT count(*) AS n FROM gold.dim_skill") is not None
    con.close()
    assert down.calls == 1  # it tried, failed, and kept yesterday's copy


def test_a_file_that_is_not_a_gold_export_is_rejected(cache: Path, source_db: Path) -> None:
    with pytest.raises(AppDataError, match="could not be downloaded"):
        connect_published(URL, cfg=CFG, transport=httpx.MockTransport(Server(b"not a database")))
    # a real database that is not a gold export (it still holds silver/bronze) is rejected too
    with pytest.raises(AppDataError, match="could not be downloaded"):
        connect_published(
            URL, cfg=CFG, transport=httpx.MockTransport(Server(source_db.read_bytes()))
        )
    assert not (cache / CFG.app.cache_dir_name / CFG.release.asset).exists()


def test_only_the_configured_release_url_is_accepted(cache: Path) -> None:
    with pytest.raises(AppDataError, match="must start with"):
        connect_published("https://evil.example/gold.duckdb", cfg=CFG)
    with pytest.raises(AppDataError, match="must start with"):
        connect({GOLD_URL_ENV: "http://github.com/akshaygovindd/jobpulse/releases/download/x"})


def test_connection_precedence_and_the_no_config_message() -> None:
    with pytest.raises(AppDataError) as e:
        connect({})
    for name in (LOCAL_DB_ENV, GOLD_URL_ENV, READ_TOKEN_ENV):
        assert name in str(e.value)


def test_publish_config_is_valid() -> None:
    cfg = load_publish()
    assert isinstance(cfg, PublishConfig)
    assert set(cfg.export.min_rows) <= GOLD_TABLES


def test_the_asset_may_not_be_called_gold() -> None:
    with pytest.raises(ValueError, match=r"gold\.duckdb"):
        load_publish().release.model_validate(
            {**load_publish().release.model_dump(), "asset": "gold.duckdb"}
        )


def test_every_page_runs_on_the_exported_file(
    source: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / load_publish().release.asset
    export_gold(source, out, CFG)
    pages = _run_pages(out, monkeypatch)
    assert len(pages) == len(PAGES)


def test_export_gold_command(
    source_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from jobpulse import cli

    db = tmp_path / "wh.duckdb"
    shutil.copy(source_db, db)
    out = tmp_path / "out" / "published.duckdb"
    assert cli.main(["export-gold", "--db", str(db), "--out", str(out)]) == cli.EXIT_ALERT
    # the fixture has far fewer rows than the production floor in config/publish.yaml
    assert "not published" in capsys.readouterr().err
    assert not out.exists()


def test_app_requirements_match_the_lock_file() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    lock = {
        p["name"]: p["version"] for p in tomllib.loads((root / "uv.lock").read_text())["package"]
    }
    pins = {}
    for line in (root / "app" / "requirements.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, version = line.partition("==")
            pins[name.strip().lower()] = version.strip()
    assert pins
    for name, version in pins.items():
        assert lock[name] == version, (
            f"app/requirements.txt pins {name}=={version}, lock has {lock[name]}"
        )
    text = (root / "app" / "requirements.txt").read_text().lower()
    assert "motherduck" not in text.replace("#", " ").split("streamlit==")[1]  # no token client


def test_publish_job_follows_the_rules() -> None:
    import yaml

    root = Path(__file__).resolve().parents[2]
    wf = yaml.safe_load((root / ".github/workflows/daily_ingest.yml").read_text())
    job = wf["jobs"]["publish"]
    cfg = load_publish().release
    assert job["needs"] == "gold"
    assert "needs.gold.result == 'success'" in job["if"]
    assert job["environment"] == "prod"
    assert job["permissions"] == {"contents": "write"}
    steps = {s["name"]: s for s in job["steps"] if "name" in s}
    text = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert cfg.tag in text
    assert cfg.asset in text
    # each secret is on exactly one step, and the AWS and OpenAI credentials are absent
    export = steps["Export gold (validated; fails without touching the published asset)"]
    upload = steps["Upload to the gold-latest release"]
    assert set(export["env"]) == {"MOTHERDUCK_TOKEN"}
    assert set(upload["env"]) == {"GH_TOKEN"}
    assert "OPENAI" not in yaml.safe_dump(job)
    assert "aws-actions" not in yaml.safe_dump(job)
    assert "if" not in export  # default: runs only if earlier steps succeeded
    assert "if" not in upload
