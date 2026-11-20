"""One shared chart style and theme for the whole app."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def test_hex_colours_live_only_in_the_palette_block() -> None:
    ui = (ROOT / "src/jobpulse/app_ui.py").read_text()
    start, end = ui.index("PRIMARY = "), ui.index("CHART_CONFIG: dict")
    outside = ui[:start] + ui[end:]
    assert not HEX.findall(outside), HEX.findall(outside)
    pages = [*sorted((ROOT / "app").rglob("*.py"))]
    for page in pages:
        assert not HEX.findall(page.read_text()), page


def test_every_chart_goes_through_the_shared_helper() -> None:
    for page in sorted((ROOT / "app").rglob("*.py")):
        text = page.read_text()
        assert "vega_lite_chart" not in text, page
        assert "bar_chart" not in text, page
        assert "line_chart" not in text, page
    ui = (ROOT / "src/jobpulse/app_ui.py").read_text()
    assert ui.count("st.vega_lite_chart(") == 1


def test_chart_specs_carry_the_shared_config_and_primary_blue() -> None:
    def script() -> None:
        import pandas as pd

        from jobpulse.app_ui import bar, date_chart

        bar(pd.DataFrame({"city": ["A", "B"], "postings": [3, 1]}), x="city", y="postings")
        date_chart(pd.DataFrame({"day": ["2026-11-11"], "n": [2]}), x="day", y="n")

    at = AppTest.from_function(script, default_timeout=20).run()
    assert not at.exception, at.exception
    charts = at.get("vega_lite_chart")
    assert len(charts) == 2
    from jobpulse.app_ui import CHART_CONFIG, PRIMARY

    for chart in charts:
        spec = json.loads(chart.proto.spec)
        assert spec["config"] == CHART_CONFIG
        assert PRIMARY in json.dumps(spec)


def test_streamlit_theme_is_blue() -> None:
    from jobpulse.app_ui import PRIMARY

    theme = tomllib.loads((ROOT / ".streamlit/config.toml").read_text())["theme"]
    assert theme["primaryColor"] == PRIMARY
    assert theme["linkColor"] == PRIMARY


# ---- Home, key insights and KPI deltas (AppTest on a fixture warehouse) -----------------------

from datetime import date  # noqa: E402

import duckdb  # noqa: E402
import pytest  # noqa: E402

from jobpulse.app_data import LOCAL_DB_ENV  # noqa: E402
from jobpulse.config import load_app  # noqa: E402
from jobpulse.ingest.storage import LocalStorage  # noqa: E402
from tests.unit.test_app import APP, _text  # noqa: E402
from tests.unit.test_gold import add_extraction, gold  # noqa: E402
from tests.unit.test_gold_visa import lca_case, load_ref  # noqa: E402
from tests.warehouse_helpers import gh_job, ingest_day, run_pipeline  # noqa: E402


@pytest.fixture(scope="module")
def history_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Four daily snapshots: 3 postings on Nov 10 and 11, a 4th on Nov 12, a 5th on Nov 13."""
    import shutil

    tmp = tmp_path_factory.mktemp("history")
    db = tmp / "wh.duckdb"
    storage = LocalStorage(tmp / "s3")
    jobs = [gh_job(str(i), content=f"text {i}") for i in range(1, 6)]
    plan = {
        date(2026, 11, 10): jobs[:3],
        date(2026, 11, 11): jobs[:3],
        date(2026, 11, 12): jobs[:4],
        date(2026, 11, 13): jobs[:5],
    }
    for day, todays in plan.items():
        ingest_day(storage, day, "greenhouse", {"acme": todays})
    bronze, silver = run_pipeline(storage, db)
    assert not bronze.failed
    assert silver.success, silver.failures
    add_extraction(
        db, "1", skills=["Python", "SQL"], salary_min=100000.0, salary_max=140000.0,
        salary_currency="USD", salary_period="year",
    )  # fmt: skip
    add_extraction(db, "2", skills=["Python"], role_family="data_scientist")
    load_ref(db, [lca_case(i) for i in range(1, 4)])
    gold(db)
    con = duckdb.connect(str(db))
    con.execute("CHECKPOINT")
    con.close()
    out = tmp / "copy.duckdb"
    shutil.copy(db, out)
    return out


def _page(db: Path, monkeypatch: pytest.MonkeyPatch, page: str | None) -> AppTest:
    monkeypatch.setenv(LOCAL_DB_ENV, str(db))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    if page:
        at.switch_page(page).run()
    assert not at.exception, at.exception
    return at


def test_home_shows_data_computed_cards_links_and_the_disclaimer(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _page(history_db, monkeypatch, None)  # Home is the default page
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Open postings"] == "5"
    assert metrics["Most requested skill"] == "python"
    assert metrics["Median advertised pay"] == "$120K"
    assert metrics["Companies with H-1B filings"] == "1 of 1"
    text = _text(at)
    assert "Data through 13 Nov 2026" in text
    assert "Evidence from public filings, not a promise" in text
    links = [str(link.proto.page) for link in at.get("page_link")]
    for page in ("market_overview", "hiring_trends", "salary", "visa", "resume_match"):
        assert any(page in link for link in links), page


def test_kpi_deltas_compare_settled_days_and_stay_neutral(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobpulse import app_ui

    monkeypatch.setattr(
        app_ui,
        "app_config",
        lambda: load_app().model_copy(
            update={"kpi": load_app().kpi.model_copy(update={"delta_days": 1})}
        ),
    )
    at = _page(history_db, monkeypatch, "app_pages/market_overview.py")
    open_postings = next(m for m in at.metric if m.label == "Open postings")
    assert open_postings.value == "5"
    # settled day Nov 12 has 4 open postings, the day before has 3
    assert open_postings.delta == "+1 vs the day before"
    assert "fully settled days" in _text(at)


def test_no_delta_when_history_is_too_short(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _page(history_db, monkeypatch, "app_pages/market_overview.py")  # default: 7 days back
    assert next(m for m in at.metric if m.label == "Open postings").delta in (None, "")
    assert "appear once the data goes back that far" in _text(at)


def test_key_insights_are_computed_from_the_data(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _page(history_db, monkeypatch, "app_pages/market_overview.py")
    text = _text(at)
    assert "Key insight." in text
    assert "has the most open postings: 5, 100% of all 5." in text
    trends = _text(_page(history_db, monkeypatch, "app_pages/hiring_trends.py"))
    assert "2 new postings across 2 days" in trends
    visa = _text(_page(history_db, monkeypatch, "app_pages/visa.py"))
    assert "certified filings for these roles" in visa
