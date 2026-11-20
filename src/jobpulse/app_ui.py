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


def pct(numerator: float, denominator: float) -> str:
    return "n/a" if not denominator else f"{100 * numerator / denominator:.0f}%"


def visa_disclaimer() -> None:
    st.info(VISA_DISCLAIMER, icon=":material/info:")


def bar(data: Any, **kwargs: Any) -> None:
    """A bar chart, or a short note when there is nothing to draw."""
    if data is None or len(data) == 0:
        st.caption("Nothing to show yet.")
    else:
        st.bar_chart(data, **kwargs)
