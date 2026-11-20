"""Shared pieces for the Streamlit pages: cached gold reads, wording, small formatters.

Pages read through `load()` only (see app_data.py for what that allows).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import duckdb
import streamlit as st

from jobpulse.app_data import LOCAL_DB_ENV, AppDataError, connect, read_gold

VISA_DISCLAIMER = (
    "Evidence from public filings, not a promise of sponsorship. LCAs are employer filings with "
    "the US Department of Labor; they do not show that any particular role is sponsored."
)
CACHE_TTL_S = 3600  # gold changes once a day; a longer cache spares MotherDuck compute hours


@st.cache_resource(show_spinner=False)
def _connection(source_key: str) -> duckdb.DuckDBPyConnection:
    return connect()


@st.cache_data(ttl=CACHE_TTL_S, max_entries=128, show_spinner=False)
def _cached(sql: str, params: tuple[Any, ...], source_key: str) -> Any:
    return read_gold(_connection(source_key), sql, params)


def load(sql: str, params: Sequence[Any] | None = None) -> Any:
    """One read-only gold query, cached for an hour. Stops the page with a clear message if the
    data cannot be reached."""
    source_key = os.environ.get(LOCAL_DB_ENV, "motherduck")
    try:
        return _cached(sql, tuple(params or ()), source_key)
    except AppDataError as e:
        st.error(str(e), icon=":material/error:")
        st.stop()


ROLE_LABELS = {
    "data_analyst": "Data Analyst",
    "data_engineer": "Data Engineer",
    "analytics_engineer": "Analytics Engineer",
    "bi": "BI Analyst / Engineer",
    "business_analyst": "Business Analyst",
    "data_scientist": "Data Scientist",
    "ml_ai_engineer": "ML/AI Engineer",
    "other": "Other roles",
}
DATA_ROLES = [r for r in ROLE_LABELS if r != "other"]
SENIORITY_LABELS = {
    "intern": "Intern",
    "junior": "Junior",
    "mid": "Mid-level",
    "senior": "Senior",
    "staff": "Staff",
    "principal": "Principal",
    "manager": "Manager",
    "director": "Director",
    "executive": "Executive",
    "unspecified": "Not stated",
}
REMOTE_LABELS = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "onsite": "On-site",
    "unspecified": "Not stated",
}
STATUS_LABELS = {
    "success": "Succeeded",
    "failed": "Failed",
    "complete": "Complete",
    "partial": "Partial",
    "skipped": "Skipped",
}
TASK_LABELS = {
    "ingest": "Fetch job boards",
    "bronze": "Load raw data",
    "silver": "Clean and link",
    "extract_filter": "Pick target roles",
    "extract": "AI extraction",
    "gold": "Build analytics",
    "lca": "Load visa filings",
}
SOURCE_LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}
MATCH_LABELS = {
    "alias": "Reviewed alias",
    "exact": "Exact name match",
    "normalized": "Name match (punctuation and Inc/LLC ignored)",
}
ACRONYMS = {"ai": "AI", "ml": "ML", "bi": "BI", "llm": "LLM", "usd": "USD", "lca": "LCA"}
NEUTRAL = ["#4C78A8", "#9AA5B1", "#7A8CA3", "#B7C4D6"]  # no red/green: colour never implies verdict


def human(value: Any, mapping: dict[str, str] | None = None) -> str:
    """A readable label for a stored code ('ml_ai_engineer' -> 'ML/AI Engineer')."""
    if value is None or value != value:  # None or NaN
        return "Not stated"
    text = str(value)
    if mapping and text in mapping:
        return mapping[text]
    words = text.replace("_", " ").split(" ")
    words = [ACRONYMS.get(w.lower(), w) for w in words]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def sentence(text: str) -> str:
    """Capitalize the first letter only (str.capitalize() would lower-case 'AI')."""
    return text[:1].upper() + text[1:]


def role_label(value: Any) -> str:
    return human(value, ROLE_LABELS)


def usd_k(value: Any) -> float | None:
    """Dollars to thousands for display with format "$%.0fK" (None stays empty)."""
    if value is None or value != value:
        return None
    return float(value) / 1000


def pct(numerator: float, denominator: float) -> str:
    return "n/a" if not denominator else f"{100 * numerator / denominator:.0f}%"


def visa_disclaimer() -> None:
    st.info(VISA_DISCLAIMER, icon=":material/info:")


def _titles(data: Any) -> tuple[Any, dict[str, str]]:
    names = {c: human(c) for c in data.columns if "_" in str(c) or str(c).islower()}
    return data.rename(columns=names), names


def _chart(data: Any, spec: dict[str, Any], height: int) -> None:
    spec = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "height": height, **spec}
    st.vega_lite_chart(data, spec, width="stretch")


def bar(
    data: Any,
    *,
    x: str,
    y: str,
    horizontal: bool = False,
    sort: str | None = None,
    labels: bool = True,
) -> None:
    """A single-colour bar chart with readable titles, value labels and full (never truncated)
    category labels. x is the category column and y the value column, as in st.bar_chart."""
    if data is None or len(data) == 0:
        st.caption("Nothing to show yet.")
        return
    data, names = _titles(data)
    cat, val = names.get(x, x), names.get(y, y)
    category: dict[str, Any] = {
        "field": cat,
        "type": "nominal",
        "axis": {"labelLimit": 600, "labelAngle": 0, "title": None},
    }
    if sort:
        category["sort"] = {
            "field": val,
            "order": "descending" if sort.startswith("-") else "ascending",
        }
    value: dict[str, Any] = {
        "field": val,
        "type": "quantitative",
        "axis": {"title": val, "format": "~s"},
    }
    cat_ch, val_ch = ("y", "x") if horizontal else ("x", "y")
    bar_layer = {"mark": {"type": "bar", "color": NEUTRAL[0]}}
    text_layer = {
        "mark": {
            "type": "text",
            "align": "left" if horizontal else "center",
            "baseline": "middle" if horizontal else "bottom",
            "dx": 4 if horizontal else 0,
            "dy": 0 if horizontal else -2,
            "color": "#44546A",
        },
        "encoding": {"text": {"field": val, "type": "quantitative", "format": ",.0f"}},
    }
    spec = {
        "layer": [bar_layer, *([text_layer] if labels else [])],
        "encoding": {
            cat_ch: category,
            val_ch: value,
            "tooltip": [
                {"field": cat, "type": "nominal"},
                {"field": val, "type": "quantitative", "format": ",.0f"},
            ],
        },
    }
    _chart(data, spec, max(120, 26 * len(data) + 30) if horizontal else 280)


def date_chart(
    data: Any, *, x: str, y: str, kind: str = "bar", series: str | None = None, y_title: str = ""
) -> None:
    """Daily/weekly chart with dates labelled 'Nov 11' (horizontal, never rotated). `data[x]` holds
    ISO date strings. With `series`, one coloured series per value, in neutral colours."""
    if data is None or len(data) == 0:
        st.caption("Nothing to show yet.")
        return
    enc: dict[str, Any] = {
        "x": {
            "field": x,
            "type": "temporal",
            "axis": {
                "format": "%b %d",
                "labelAngle": 0,
                "title": None,
                "tickCount": {"interval": "day", "step": 1},
                "labelOverlap": True,
            },
        },
        "y": {"field": y, "type": "quantitative", "title": y_title or human(y)},
        "tooltip": [
            {"field": x, "type": "temporal", "format": "%b %d"},
            {"field": y, "type": "quantitative", "format": ",.2~f"},
        ],
    }
    mark: dict[str, Any] = {"type": "bar" if kind == "bar" else "line"}
    if kind == "line":
        mark["point"] = True
    if series:
        enc["color"] = {
            "field": series,
            "type": "nominal",
            "scale": {"range": NEUTRAL},
            "legend": {"title": None, "orient": "bottom"},
        }
        enc["tooltip"].insert(1, {"field": series, "type": "nominal"})
    else:
        mark["color"] = NEUTRAL[0]
    _chart(data, {"mark": mark, "encoding": enc}, 280)


def salary_chart(data: Any) -> None:
    """Grouped horizontal bars: role family on the axis, seniority as colour, '$NNNK' on each bar.
    `data` has columns Role family, Seniority and Median (thousands of dollars)."""
    if data is None or len(data) == 0:
        st.caption("Nothing to show yet.")
        return
    present = set(data["Seniority"])
    order = [v for v in SENIORITY_LABELS.values() if v in present]
    shades = [
        "#C6DBEF", "#9ECAE1", "#6BAED6", "#4292C6", "#2171B5",
        "#08519C", "#08306B", "#7A8CA3", "#9AA5B1", "#B7C4D6",
    ]  # fmt: skip
    y = {
        "field": "Role family",
        "type": "nominal",
        "axis": {"labelLimit": 600, "title": None},
        "sort": {"field": "Median", "op": "max", "order": "descending"},
    }
    y_off = {"field": "Seniority", "sort": order}
    spec = {
        "transform": [{"calculate": "'$' + format(datum.Median, '.0f') + 'K'", "as": "label"}],
        "encoding": {
            "y": y,
            "yOffset": y_off,
            "x": {
                "field": "Median",
                "type": "quantitative",
                "title": "Median advertised salary ($K)",
            },
        },
        "layer": [
            {
                "mark": "bar",
                "encoding": {
                    "color": {
                        "field": "Seniority",
                        "type": "nominal",
                        "scale": {"domain": order, "range": shades[: len(order)]},
                        "legend": {"title": "Seniority", "orient": "bottom"},
                    },
                    "tooltip": [
                        {"field": "Role family", "type": "nominal"},
                        {"field": "Seniority", "type": "nominal"},
                        {"field": "label", "type": "nominal", "title": "Median"},
                    ],
                },
            },
            {
                "mark": {"type": "text", "align": "left", "baseline": "middle", "dx": 4,
                         "color": "#44546A", "fontSize": 11},
                "encoding": {"text": {"field": "label", "type": "nominal"}},
            },
        ],
    }  # fmt: skip
    rows = data["Role family"].nunique() * max(1, data["Seniority"].nunique())
    _chart(data, spec, max(200, 22 * rows + 70))
