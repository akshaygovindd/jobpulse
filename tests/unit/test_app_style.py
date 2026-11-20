"""One shared chart style and theme for the whole app."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
from tests.unit.test_gold import add_extraction, gold, seed  # noqa: E402
from tests.unit.test_gold_visa import lca_case, load_ref  # noqa: E402
from tests.warehouse_helpers import gh_job, ingest_day, run_pipeline  # noqa: E402


@pytest.fixture(scope="module")
def history_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Four daily snapshots: 3 postings on Nov 10 and 11, a 4th on Nov 12, a 5th on Nov 13."""
    import shutil

    tmp = tmp_path_factory.mktemp("history")
    db = tmp / "wh.duckdb"
    storage = LocalStorage(tmp / "s3")
    jobs = [gh_job(str(i), content=f"text {i}", loc="New York, NY") for i in range(1, 6)]
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
    assert metrics["Most requested skill"] == "Python"
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


def test_map_shows_open_postings_on_a_dark_carto_basemap_with_attribution(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _page(history_db, monkeypatch, "app_pages/market_overview.py")
    decks = at.get("deck_gl_json_chart")
    assert len(decks) == 1
    spec = json.loads(decks[0].proto.json)
    assert "dark-matter-gl-style" in decks[0].proto.json
    layer = spec["layers"][0]
    assert layer["data"][0]["city"] == "New York, New York"
    assert layer["data"][0]["postings"] == 5
    assert "mapbox" not in decks[0].proto.json.lower()  # no token-based basemap
    text = _text(at)
    assert load_app().map.attribution in text
    assert "5 open postings that name a single city are on the map; 0 more" in text
    assert "approximate city centres" in text


def test_map_points_sum_variants_and_report_what_is_left_off() -> None:
    from jobpulse.app_ui import map_points

    coords = {"São Paulo, Brazil": (-23.5, -46.6), "Sao Paulo, Brazil": (-23.5, -46.6),
              "Boston, Massachusetts": (42.36, -71.06)}  # fmt: skip
    rows = [("São Paulo, Brazil", 25), ("Sao Paulo, Brazil", 18), ("Boston, Massachusetts", 43),
            ("Nowhereville", 7)]  # fmt: skip
    points, left_off = map_points(rows, coords, 4, 36)
    assert left_off == 7
    assert [(p["postings"]) for p in points] == [43, 43]  # both cities have 43
    assert all(4 <= p["radius"] <= 36 for p in points)
    assert max(p["radius"] for p in points) == 36
    one, _ = map_points([("Boston, Massachusetts", 4), ("São Paulo, Brazil", 1)], coords, 4, 36)
    assert one[0]["radius"] == 36
    assert one[1]["radius"] == pytest.approx(4 + 32 * 0.5)  # sqrt(1/4) = 0.5


def test_city_coordinates_and_app_config_are_valid() -> None:
    from jobpulse.config import load_city_coords

    coords = load_city_coords()
    assert coords["New York, New York"] == (40.7128, -74.006)
    assert len(coords) >= 80
    assert all(-90 <= la <= 90 and -180 <= lo <= 180 for la, lo in coords.values())
    cfg = load_app()
    assert "CARTO" in cfg.map.attribution
    assert "OpenStreetMap" in cfg.map.attribution


def test_visa_company_lookup_card(history_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _page(history_db, monkeypatch, "app_pages/visa.py")
    assert "Company lookup" in [s.value for s in at.subheader]
    assert at.selectbox(key="visa_company").label == "Search for a company"
    text = _text(at)
    assert "-badge[" in text  # evidence class badge
    assert "Most certified filings came in FY" in text
    assert "Evidence from public filings, not a promise of sponsorship." in text
    labels = {m.label for m in at.metric}
    assert {"Certified filings", "Withdrawn or denied", "Open postings"} <= labels
    assert any(label.endswith("certified") and label.startswith("FY") for label in labels)


# ---- review fixes: labels, wide bars, heatmap, skill names, gated trends, dashes -------------


def _spec_of(script: Callable[[], None]) -> dict[str, Any]:
    at = AppTest.from_function(script, default_timeout=20).run()
    assert not at.exception, at.exception
    return dict(json.loads(at.get("vega_lite_chart")[0].proto.spec))


def test_horizontal_bars_ask_for_every_category_label() -> None:
    def script() -> None:
        import pandas as pd

        from jobpulse.app_ui import bar

        frame = pd.DataFrame(
            {"policy": ["Not stated", "On-site", "Hybrid", "Remote"], "postings": [9, 5, 3, 2]}
        )
        bar(frame, x="policy", y="postings", horizontal=True, sort="-postings")

    spec = _spec_of(script)
    axis = spec["encoding"]["y"]["axis"]
    assert axis["values"] == ["Not stated", "On-site", "Hybrid", "Remote"]
    assert axis["labelOverlap"] is False
    assert spec["height"] >= 30 * 4  # room for every row


def test_daily_bars_are_wide_bands_and_lines_stay_temporal() -> None:
    def bars() -> None:
        import pandas as pd

        from jobpulse.app_ui import date_chart

        date_chart(pd.DataFrame({"day": ["2026-11-11", "2026-11-12"], "n": [2, 3]}), x="day", y="n")

    def lines() -> None:
        import pandas as pd

        from jobpulse.app_ui import date_chart

        frame = pd.DataFrame({"week": ["2026-11-09", "2026-11-16"], "s": [1, 2]})
        date_chart(frame, x="week", y="s", kind="line")

    b = _spec_of(bars)
    assert b["encoding"]["x"]["type"] == "ordinal"
    assert b["encoding"]["x"]["timeUnit"] == "yearmonthdate"
    assert b["mark"]["width"] == {"band": 0.7}
    assert _spec_of(lines)["encoding"]["x"]["type"] == "temporal"


def test_salary_chart_is_a_labelled_heatmap() -> None:
    def script() -> None:
        import pandas as pd

        from jobpulse.app_ui import salary_chart

        salary_chart(
            pd.DataFrame(
                {
                    "Role family": ["Data Scientist", "Data Scientist", "ML/AI Engineer"],
                    "Seniority": ["Mid-level", "Senior", "Senior"],
                    "Median": [150.0, 190.0, 240.0],
                    "n_annualized": [6, 9, 12],
                }
            )
        )

    spec = _spec_of(script)
    marks = [layer["mark"]["type"] for layer in spec["layer"]]
    assert marks == ["rect", "text"]
    assert spec["encoding"]["x"]["sort"] == ["Mid-level", "Senior"]
    assert "'$' + format(datum.Median, '.0f') + 'K'" in spec["transform"][0]["calculate"]
    assert {"field": "n_annualized", "type": "quantitative", "title": "Salaries"} in spec["layer"][
        0
    ]["encoding"]["tooltip"]


def test_skill_names_show_proper_casing_but_data_stays_canonical() -> None:
    from jobpulse.app_ui import skill_label
    from jobpulse.config import load_skill_display
    from jobpulse.skills import norm_skill

    assert [skill_label(s) for s in ("sql", "python", "dbt", "aws", "snowflake", "airflow")] == [
        "SQL", "Python", "dbt", "AWS", "Snowflake", "Airflow",
    ]  # fmt: skip
    assert [skill_label(s) for s in ("looker", "kubernetes", "databricks", "pandas", "mlops")] == [
        "Looker", "Kubernetes", "Databricks", "pandas", "MLOps",
    ]  # fmt: skip
    assert skill_label("stream processing") == "Stream processing"  # unlisted: capitalized only
    # every key is a canonical name, so display names can never drift from the data
    for canonical, shown in load_skill_display().items():
        assert norm_skill(canonical) == canonical, canonical
        assert norm_skill(shown) == canonical, (canonical, shown)


def test_full_weeks_counts_complete_monday_to_sunday_weeks() -> None:
    from jobpulse.insights import full_weeks

    assert full_weeks(date(2026, 11, 10), date(2026, 11, 20)) == 0  # data starts on a Tuesday
    assert full_weeks(date(2026, 11, 10), date(2026, 11, 22)) == 1  # Nov 16-22 is complete
    assert full_weeks(date(2026, 11, 9), date(2026, 11, 22)) == 2
    assert full_weeks(date(2026, 11, 10), date(2026, 12, 6)) == 3
    assert full_weeks(date(2026, 11, 10), date(2026, 12, 5)) == 2


def test_skills_over_time_is_hidden_until_enough_full_weeks(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobpulse import app_ui

    small = app_ui.app_config().model_copy(
        update={"samples": app_ui.app_config().samples.model_copy(update={"min_group_n": 1})}
    )  # the fixture is tiny; the real floor is exercised in test_thin_roles_are_not_offered
    monkeypatch.setattr(app_ui, "app_config", lambda: small)
    at = _page(history_db, monkeypatch, "app_pages/hiring_trends.py")
    assert "Skills over time (weeks start on Mondays)" not in [m.label for m in at.multiselect]
    text = _text(at)
    assert "Skills over time appears once there are at least 3 full weeks" in text
    assert "there are 0 so far" in text
    from jobpulse import insights as ins

    monkeypatch.setattr(ins, "full_weeks", lambda first, last: 3)
    shown = _page(history_db, monkeypatch, "app_pages/hiring_trends.py")
    assert "Skills over time (weeks start on Mondays)" in [m.label for m in shown.multiselect]


def test_pipeline_health_tables_show_dashes_not_none(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobpulse.app_ui import dash

    assert dash(None) == "—"
    assert dash(float("nan")) == "—"
    import pandas as pd

    assert dash(pd.NA) == "—"
    assert dash(1234.4) == "1,234"
    assert dash(73.2, "{:.0f}%") == "73%"
    at = _page(history_db, monkeypatch, "app_pages/pipeline_health.py")
    for frame in (d.value for d in at.dataframe):
        assert not frame.isin([None, "None", "nan", "NaN"]).any().any()


def test_pipeline_health_separates_eval_cost_counts_boards_once_and_explains_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from datetime import UTC, datetime

    from jobpulse.config import load_extraction, load_pipeline
    from jobpulse.extract.store import ensure_extract_tables
    from jobpulse.ops.load_ops import ensure_ops_tables

    db = tmp_path / "wh.duckdb"
    now = datetime(2026, 11, 20, 8, tzinfo=UTC)
    later = datetime(2026, 11, 20, 12, tzinfo=UTC)
    seed(LocalStorage(tmp_path / "s3"), db, [gh_job("1"), gh_job("2", content="x")])
    add_extraction(db, "1", skills=["Python"])
    con = duckdb.connect(str(db))
    ensure_ops_tables(con, load_pipeline().warehouse)
    ensure_extract_tables(con, load_extraction())
    runs = [("a1", "silver", "failed", now), ("a2", "silver", "success", later)]
    for attempt, task, status, at_time in runs:
        con.execute(
            "INSERT INTO ops.pipeline_runs VALUES (?, DATE '2026-11-20', ?, 'greenhouse', ?, ?, "
            "5.0, ?, 1, 1, 0, 10, 10, NULL, 0.0, 'prod')",
            [attempt, task, at_time, at_time, status],
        )
    for attempt, auth in (("a1", False), ("a2", True), ("a3", True)):  # 3 attempts, 2 kept
        con.execute(
            "INSERT INTO ops.company_snapshots VALUES (?, DATE '2026-11-20', 'greenhouse', ?, 'X', "
            "'complete', NULL, NULL, 1, 2, 'k', ?, 'set', ?, ?)",
            [attempt, f"b{attempt}", auth, now, now],
        )
    for i, (mode, cost) in enumerate([("sync", 0.10), ("sync", 0.20), ("eval", 0.50)]):
        con.execute(
            "INSERT INTO extract.llm_calls VALUES (?,?,?,'m1', ?, 100, 50, 0, ?, 10, 'ok', ?)",
            [f"c{i}", "a1", f"h{i}", mode, cost, now],
        )
    con.close()
    gold(db)
    con = duckdb.connect(str(db))
    con.execute("CHECKPOINT")
    con.close()
    out = tmp_path / "copy.duckdb"
    shutil.copy(db, out)
    at = _page(out, monkeypatch, "app_pages/pipeline_health.py")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Pipeline spend"] == "$0.30"  # the eval call is not in it
    assert metrics["AI calls"] == "2"
    assert metrics["Model evaluation (one-off)"] == "$0.50"
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "1 of the last 2 runs did not succeed" in warnings
    assert "Clean and link" in warnings
    assert "later succeeded" in warnings


def test_every_page_warns_when_the_data_is_older_than_the_freshness_limit(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the fixture's snapshots are from 2026-11-13, far older than max_age_hours
    for page in ("app_pages/market_overview.py", "app_pages/salary.py"):
        at = _page(history_db, monkeypatch, page)
        assert any("last refreshed on" in str(w.value) for w in at.warning), page
