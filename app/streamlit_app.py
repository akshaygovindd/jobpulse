"""JobPulse: job-market intelligence for data and analytics roles.

Reads the gold layer only (see src/jobpulse/app_data.py). Run locally:

    JOBPULSE_APP_DB=path/to/warehouse.duckdb uv run streamlit run app/streamlit_app.py

The public deploy sets JOBPULSE_GOLD_URL (the published gold file; no token of any kind).
"""

import sys
from pathlib import Path

import streamlit as st

# On Streamlit Community Cloud the project is not pip-installed; make `jobpulse` importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

st.set_page_config(page_title="JobPulse", page_icon=":material/monitoring:", layout="wide")

page = st.navigation(
    [
        st.Page(
            "app_pages/home.py",
            title="Home",
            icon=":material/home:",
            default=True,
            help="What JobPulse is, with the headline numbers.",
        ),
        st.Page(
            "app_pages/market_overview.py",
            title="Market overview",
            icon=":material/public:",
            help="Open postings by company, role family and remote policy, with data coverage.",
        ),
        st.Page(
            "app_pages/hiring_trends.py",
            title="Hiring trends",
            icon=":material/trending_up:",
            help="New postings per week and the skills employers ask for, by role family.",
        ),
        st.Page(
            "app_pages/salary.py",
            title="Salary",
            icon=":material/payments:",
            help="Advertised salary ranges (with coverage) and, separately, wages on LCA filings.",
        ),
        st.Page(
            "app_pages/visa.py",
            title="Visa intelligence",
            icon=":material/flight_takeoff:",
            help="Public H-1B filing evidence per company and what postings say about sponsorship.",
        ),
        st.Page(
            "app_pages/resume_match.py",
            title="Resume match",
            icon=":material/description:",
            help="Paste a resume and rank open data and AI postings by skill overlap.",
        ),
        st.Page(
            "app_pages/pipeline_health.py",
            title="Pipeline health",
            icon=":material/health_and_safety:",
            help="How fresh the data is, recent runs, data quality and AI extraction cost.",
        ),
    ],
    position="top",
)
if page.title != "Home":  # Home has its own hero
    st.title(page.title, icon=page.icon)
page.run()
