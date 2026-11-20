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
NEUTRAL = ["#4C78A8", "#9AA5B1", "#7A8CA3", "#B7C4D6"]  # no red/green: colour never implies verdict


def human(value: Any, mapping: dict[str, str] | None = None) -> str:
    """A readable label for a stored code ('ml_ai_engineer' -> 'ML/AI Engineer')."""
    if value is None or value != value:  # None or NaN
        return "Not stated"
    text = str(value)
    if mapping and text in mapping:
        return mapping[text]
    return text.replace("_", " ").capitalize()


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


def bar(data: Any, **kwargs: Any) -> None:
    """A bar chart with readable axis titles, or a short note when there is nothing to draw."""
    if data is None or len(data) == 0:
        st.caption("Nothing to show yet.")
        return
    names = {c: human(c) for c in data.columns if "_" in str(c) or str(c).islower()}
    data = data.rename(columns=names)
    for key in ("x", "y", "color"):
        value = kwargs.get(key)
        if isinstance(value, str):
            kwargs[key] = names.get(value, value)
        elif isinstance(value, list):
            kwargs[key] = [names.get(v, v) for v in value]
    if isinstance(kwargs.get("sort"), str) and kwargs["sort"].lstrip("-") in names:
        sign = "-" if kwargs["sort"].startswith("-") else ""
        kwargs["sort"] = sign + names[kwargs["sort"].lstrip("-")]
    if "color" not in kwargs:
        kwargs["color"] = NEUTRAL[0]
    st.bar_chart(data, **kwargs)
