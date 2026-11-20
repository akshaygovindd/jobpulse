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
