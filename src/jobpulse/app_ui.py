"""Shared pieces for the Streamlit pages: cached gold reads, wording, small formatters.

Pages read through `load()` only (see app_data.py for what that allows).
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import streamlit as st

from jobpulse.app_data import GOLD_URL_ENV, LOCAL_DB_ENV, AppDataError, connect, read_gold
from jobpulse.config import AppConfig, load_app, load_city_coords, load_skill_display

VISA_DISCLAIMER = (
    "Evidence from public filings, not a promise of sponsorship. LCAs are employer filings with "
    "the US Department of Labor; they do not show that any particular role is sponsored."
)
CACHE_TTL_S = 3600  # gold changes once a day; a longer cache spares MotherDuck compute hours


@st.cache_resource(ttl=CACHE_TTL_S, show_spinner=False)
def _connection(source_key: str) -> duckdb.DuckDBPyConnection:
    env = dict(os.environ)
    if GOLD_URL_ENV not in env:  # Community Cloud "Secrets" are also readable as st.secrets
        try:
            url = st.secrets.get(GOLD_URL_ENV)
        except Exception:  # no secrets file: running locally
            url = None
        if isinstance(url, str) and url:
            env[GOLD_URL_ENV] = url
    return connect(env)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=128, show_spinner=False)
def _cached(sql: str, params: tuple[Any, ...], source_key: str) -> Any:
    return read_gold(_connection(source_key), sql, params)


def load(sql: str, params: Sequence[Any] | None = None) -> Any:
    """One read-only gold query, cached for an hour. Stops the page with a clear message if the
    data cannot be reached."""
    source_key = os.environ.get(LOCAL_DB_ENV, "published")
    try:
        return _cached(sql, tuple(params or ()), source_key)
    except AppDataError as e:
        # Visitors see only the public text; the detail goes to the server log.
        logging.getLogger("jobpulse.app").warning("app data error: %s", e)
        st.error(e.public, icon=":material/error:")
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
# One chart style for the whole app. Blue and slate only: colour never implies a verdict (no
# red/green). Every chart goes through `_chart`, which applies CHART_CONFIG, so changing the look
# means editing this block (and `.streamlit/config.toml` for the Streamlit theme).
PRIMARY = "#2F6DB5"  # same blue as the Streamlit theme's primaryColor
NEUTRAL = ["#4C78A8", "#9AA5B1", "#7A8CA3", "#B7C4D6"]
HEATMAP_RAMP = ["#DCE9F7", "#9ECAE1", "#4292C6", "#2171B5", "#08306B"]  # light to dark blue
TEXT_ON_LIGHT = "#1B2A3A"
TEXT_ON_DARK = "#FFFFFF"
LABEL_COLOR = "#7F8FA6"  # mid slate: readable on both the light and the dark Streamlit theme
CHART_CONFIG: dict[str, Any] = {
    "font": "sans-serif",
    "view": {"stroke": None},
    "axis": {
        "labelOverlap": False,  # never hide a category label
        "labelFontSize": 12,
        "titleFontSize": 12,
        "titleFontWeight": "normal",
        "gridOpacity": 0.25,
        "domainOpacity": 0.4,
        "tickOpacity": 0.4,
    },
    "legend": {"labelFontSize": 12, "titleFontSize": 12},
    "bar": {"cornerRadiusEnd": 2},
    "line": {"strokeWidth": 2},
}


def human(value: Any, mapping: dict[str, str] | None = None) -> str:
    """A readable label for a stored code ('ml_ai_engineer' -> 'ML/AI Engineer')."""
    if is_missing(value):
        return "Not stated"
    text = str(value)
    if mapping and text in mapping:
        return mapping[text]
    words = text.replace("_", " ").split(" ")
    words = [ACRONYMS.get(w.lower(), w) for w in words]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def is_missing(value: Any) -> bool:
    """True for None, NaN and pandas' NA (which has no truth value)."""
    try:
        return value is None or bool(value != value)
    except TypeError:
        return True


def clean(value: Any, default: str = "") -> str:
    """Text for a table cell or sentence: `default` for None, NaN, NA and blank strings. NaN is a
    truthy float, so `value or default` would let 'nan' and 'None' through."""
    if is_missing(value):
        return default
    text = str(value).strip()
    return text or default


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
    """A share as text. 99.7% is shown as 99.7%, not rounded up to 100% (which would claim that
    nothing is missing), and a tiny non-zero share is "<1%"."""
    if not denominator:
        return "n/a"
    value = 100 * numerator / denominator
    if 99.5 <= value < 100:
        return f"{min(value, 99.9):.1f}%"
    if 0 < value < 0.5:
        return "<1%"
    return f"{value:.0f}%"


def visa_disclaimer() -> None:
    st.info(VISA_DISCLAIMER, icon=":material/info:")


def _titles(data: Any) -> tuple[Any, dict[str, str]]:
    names = {c: human(c) for c in data.columns if "_" in str(c) or str(c).islower()}
    return data.rename(columns=names), names


def _chart(data: Any, spec: dict[str, Any], height: int) -> None:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "height": height,
        "config": CHART_CONFIG,
        **spec,
    }
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
        "axis": {
            "labelLimit": 600,
            "labelAngle": 0,
            "title": None,
            "labelOverlap": False,
            "values": [str(v) for v in data[cat]],  # one tick, hence one label, per category
        },
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
    bar_layer = {"mark": {"type": "bar", "color": PRIMARY}}
    text_layer = {
        "mark": {
            "type": "text",
            "align": "left" if horizontal else "center",
            "baseline": "middle" if horizontal else "bottom",
            "dx": 4 if horizontal else 0,
            "dy": 0 if horizontal else -2,
            "color": LABEL_COLOR,
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
    _chart(data, spec, max(120, 30 * len(data) + 40) if horizontal else 280)


def date_chart(
    data: Any, *, x: str, y: str, kind: str = "bar", series: str | None = None, y_title: str = ""
) -> None:
    """Daily/weekly chart with dates labelled 'Nov 11' (horizontal, never rotated). `data[x]` holds
    ISO date strings. Bars use one band per day (so they are wide, never thin lines); lines use a
    time axis. With `series`, one coloured series per value, in neutral colours."""
    if data is None or len(data) == 0:
        st.caption("Nothing to show yet.")
        return
    axis = {"format": "%b %d", "labelAngle": 0, "title": None, "labelOverlap": True}
    if kind == "bar":
        x_enc: dict[str, Any] = {
            "field": x,
            "type": "ordinal",
            "timeUnit": "yearmonthdate",
            "axis": axis,
        }
    else:
        x_enc = {
            "field": x,
            "type": "temporal",
            "axis": {**axis, "tickCount": {"interval": "day", "step": 1}},
        }
    enc: dict[str, Any] = {
        "x": x_enc,
        "y": {"field": y, "type": "quantitative", "title": y_title or human(y)},
        "tooltip": [
            {"field": x, "type": "temporal", "format": "%b %d"},
            {"field": y, "type": "quantitative", "format": ",.2~f"},
        ],
    }
    mark: dict[str, Any] = {"type": "bar" if kind == "bar" else "line"}
    if kind == "bar":
        mark["width"] = {"band": 0.7}
    else:
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
        mark["color"] = PRIMARY
    _chart(data, {"mark": mark, "encoding": enc}, 280)


def salary_chart(data: Any) -> None:
    """Heatmap of median advertised pay: role family by seniority, '$NNNK' in every cell, darker
    for higher pay. `data` has columns Role family, Seniority and Median (thousands of dollars),
    and optionally n_annualized (shown in the tooltip). Cells with no group stay empty."""
    if data is None or len(data) == 0:
        st.caption("Nothing to show yet.")
        return
    present = set(data["Seniority"])
    order = [v for v in SENIORITY_LABELS.values() if v in present]
    lo, hi = float(data["Median"].min()), float(data["Median"].max())
    switch = lo + 0.55 * (hi - lo)  # darker than this: white text
    tooltip = [
        {"field": "Role family", "type": "nominal"},
        {"field": "Seniority", "type": "nominal"},
        {"field": "label", "type": "nominal", "title": "Median"},
    ]
    if "n_annualized" in data.columns:
        tooltip.append({"field": "n_annualized", "type": "quantitative", "title": "Salaries"})
    spec = {
        "transform": [{"calculate": "'$' + format(datum.Median, '.0f') + 'K'", "as": "label"}],
        "encoding": {
            "x": {
                "field": "Seniority",
                "type": "ordinal",
                "sort": order,
                "axis": {"title": None, "labelAngle": 0, "orient": "top", "labelOverlap": False},
            },
            "y": {
                "field": "Role family",
                "type": "ordinal",
                "sort": {"field": "Median", "op": "max", "order": "descending"},
                "axis": {"title": None, "labelLimit": 600, "labelOverlap": False},
            },
        },
        "layer": [
            {
                "mark": {"type": "rect", "stroke": TEXT_ON_DARK, "strokeWidth": 1},
                "encoding": {
                    "color": {
                        "field": "Median",
                        "type": "quantitative",
                        "scale": {"range": HEATMAP_RAMP},
                        "legend": {"title": "Median ($K)", "orient": "bottom"},
                    },
                    "tooltip": tooltip,
                },
            },
            {
                "mark": {"type": "text", "fontSize": 12, "fontWeight": "bold"},
                "encoding": {
                    "text": {"field": "label", "type": "nominal"},
                    "color": {
                        "condition": {"test": f"datum.Median > {switch}", "value": TEXT_ON_DARK},
                        "value": TEXT_ON_LIGHT,
                    },
                },
            },
        ],
    }
    _chart(data, spec, max(160, 44 * data["Role family"].nunique() + 90))


def salary_range(low: Any, high: Any, currency: Any) -> str:
    """'$120K-$160K' from annualized advertised salary; 'Not advertised' when there is none."""
    lo, hi = usd_k(low), usd_k(high)
    if lo is None and hi is None:
        return "Not advertised"
    sign = "$" if currency == "USD" else f"{currency} " if isinstance(currency, str) else ""
    if lo is not None and hi is not None and round(lo) != round(hi):
        return f"{sign}{lo:.0f}K to {sign}{hi:.0f}K"
    return f"{sign}{(lo if lo is not None else hi):.0f}K"


@functools.lru_cache(maxsize=1)
def city_coords() -> dict[str, tuple[float, float]]:
    """config/city_coords.yaml, parsed once per process (it is hand-entered and static)."""
    return load_city_coords(app_config())


@functools.lru_cache(maxsize=1)
def app_config() -> AppConfig:
    return load_app()


def key_insight(text: str | None) -> None:
    """A one-sentence, data-computed takeaway above a chart. Nothing is shown without data."""
    if text:
        st.markdown(f":material/lightbulb: **Key insight.** {text}")
        st.space("small")  # room above the chart, so its hover toolbar cannot cover the text


def stale_banner() -> None:
    """A banner on every page when the newest snapshot is older than the configured freshness
    limit (age is computed at view time, so it is right between daily builds)."""
    row = load(
        "SELECT max(newest_completed_at) AS newest, max(max_age_hours) AS limit_h "
        "FROM gold.fct_snapshot_freshness"
    ).iloc[0]
    if is_missing(row["newest"]) or is_missing(row["limit_h"]):
        return
    newest = row["newest"].to_pydatetime()
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    age_h = (datetime.now(UTC) - newest).total_seconds() / 3600
    if age_h > float(row["limit_h"]):
        st.warning(
            f"The data was last refreshed on {newest:%d %b %Y} ({age_h / 24:.0f} days ago), so "
            "the numbers may be behind.",
            icon=":material/schedule:",
        )


def kpi_label(days: int) -> str:
    if days == 7:
        return "vs last week"
    return "vs the day before" if days == 1 else f"vs {days} days ago"


_ACTIVE_ON = (
    "SELECT count(*) AS postings, count(DISTINCT company_id) AS companies, "
    "count(DISTINCT city) AS cities FROM gold.fct_postings "
    "WHERE NOT is_probable_duplicate AND first_seen_date <= CAST(? AS DATE) "
    "AND (closed_date IS NULL OR closed_date > CAST(? AS DATE))"
)


def kpi_basis(days: int) -> str | None:
    """'19 Nov vs 12 Nov': the two settled days a KPI change compares (None without data)."""
    latest = load("SELECT max(newest_snapshot_date) AS d FROM gold.fct_snapshot_freshness")[
        "d"
    ].iloc[0]
    if is_missing(latest):
        return None
    latest_day = latest.date() if hasattr(latest, "date") else latest
    settled = latest_day - timedelta(days=1)
    return f"{settled:%d %b} vs {settled - timedelta(days=days):%d %b}"


def kpi_comparison(days: int) -> tuple[dict[str, int], dict[str, int] | None]:
    """Open postings, companies hiring and cities on the newest *settled* day and `days` before.

    A posting is closed only after it is missing from two consecutive snapshots, so on the newest
    snapshot day itself closures are not known yet and the count would be too high. Both values
    therefore use the day before the newest snapshot, rebuilt the same way (first seen on or before
    the day, not closed by then), so they are comparable. The earlier value is None when the data
    does not go back that far: the first day of data is a baseline."""
    latest = load("SELECT max(newest_snapshot_date) AS d FROM gold.fct_snapshot_freshness")[
        "d"
    ].iloc[0]
    first = load("SELECT min(first_seen_date) AS d FROM gold.fct_postings")["d"].iloc[0]
    if latest is None or latest != latest or first is None or first != first:
        return {"postings": 0, "companies": 0, "cities": 0}, None
    latest_day = latest.date() if hasattr(latest, "date") else latest
    first_day = first.date() if hasattr(first, "date") else first
    settled: date = latest_day - timedelta(days=1)
    ref_day: date = settled - timedelta(days=days)

    def at(day: date) -> dict[str, int]:
        row = load(_ACTIVE_ON, [day.isoformat(), day.isoformat()]).iloc[0]
        return {k: int(row[k]) for k in ("postings", "companies", "cities")}

    return at(settled), (at(ref_day) if ref_day >= first_day else None)


def map_points(
    rows: Sequence[tuple[str, int]],
    coords: dict[str, tuple[float, float]],
    min_radius: float,
    max_radius: float,
) -> tuple[list[dict[str, Any]], int]:
    """(points to draw, postings left off). Cities that share a coordinate (spelling variants) are
    summed; the dot radius grows with the square root of postings, between the two radii."""
    points: dict[tuple[float, float], dict[str, Any]] = {}
    left_off = 0
    for city, n in rows:
        if city in coords:
            lat, lon = coords[city]
            p = points.setdefault((lat, lon), {"city": city, "postings": 0, "lat": lat, "lon": lon})
            p["postings"] += int(n)
        else:
            left_off += int(n)
    data = sorted(points.values(), key=lambda p: -p["postings"])
    if data:
        top = data[0]["postings"]
        for p in data:
            p["radius"] = min_radius + (max_radius - min_radius) * (p["postings"] / top) ** 0.5
    return data, left_off


def city_map(rows: Sequence[tuple[str, int]]) -> tuple[int, int]:
    """Draw open postings per city on a dark CARTO basemap. Returns (postings drawn, postings left
    off because the city has no coordinates in config/city_coords.yaml)."""
    import pydeck as pdk

    cfg = app_config().map
    data, left_off = map_points(rows, city_coords(), cfg.min_radius_px, cfg.max_radius_px)
    if not data:
        st.caption("No mappable cities yet.")
        return 0, left_off
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=data,
        get_position="[lon, lat]",
        get_radius="radius",
        radius_units="pixels",
        get_fill_color=[47, 109, 181, 190],
        get_line_color=[255, 255, 255, 120],
        line_width_min_pixels=1,
        stroked=True,
        pickable=True,
    )
    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=pdk.ViewState(
            latitude=cfg.initial_lat, longitude=cfg.initial_lon, zoom=cfg.initial_zoom
        ),
        map_provider="carto",
        map_style=pdk.map_styles.CARTO_DARK,
        tooltip={"text": "{city}: {postings} open postings"},
    )
    st.pydeck_chart(deck, height=420)
    st.caption(cfg.attribution)
    return sum(p["postings"] for p in data), left_off


@functools.lru_cache(maxsize=1)
def _skill_names() -> dict[str, str]:
    return load_skill_display()


def skill_label(name: str) -> str:
    """Display name of a canonical skill: SQL, Python, dbt, AWS... Unlisted skills keep their
    canonical spelling with the first letter capitalized. Data is never changed."""
    names = _skill_names()
    if name in names:
        return names[name]
    return name[:1].upper() + name[1:]


def dash(value: Any, fmt: str = "{:,.0f}") -> str:
    """A table cell for a value that may be missing: the formatted number, or an em dash."""
    return "—" if is_missing(value) else fmt.format(value)
