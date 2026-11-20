import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    app_config,
    kpi_comparison,
    kpi_label,
    load,
    pct,
    visa_disclaimer,
)

GITHUB_URL = "https://github.com/akshaygovindd/jobpulse"

counts = load(
    "SELECT count(*) AS n, count(*) FILTER (WHERE is_active) AS active, "
    "count(DISTINCT company_id) FILTER (WHERE is_active) AS companies "
    "FROM gold.fct_postings WHERE NOT is_probable_duplicate"
).iloc[0]
if not counts["n"]:
    st.title("JobPulse")
    st.warning("No postings yet. Run the pipeline, then `jobpulse build-gold`.")
    st.stop()

latest = load("SELECT max(newest_snapshot_date) AS d FROM gold.fct_snapshot_freshness")["d"].iloc[0]

# ---- hero ------------------------------------------------------------------------------------
st.markdown("# JobPulse")
st.markdown(
    "### Daily job-market intelligence for data and AI roles\n"
    "What is open, what it pays, which skills employers ask for, and which employers have a "
    "public record of H-1B filings, from the job boards of "
    f"{int(counts['companies'])} tech companies, refreshed every day."
)
with st.container(horizontal=True, vertical_alignment="center"):
    if latest is not None and latest == latest:
        st.caption(f"Data through {latest:%d %b %Y}")
    st.link_button("View the project on GitHub", GITHUB_URL, icon=":material/code:")

# ---- insight cards (all numbers computed from gold) ------------------------------------------
cfg = app_config()
days = cfg.kpi.delta_days
now_kpi, before_kpi = kpi_comparison(days)
open_delta = insights.delta_text(
    now_kpi["postings"],
    None if before_kpi is None else before_kpi["postings"],
    unit=kpi_label(days),
)

target = load(
    "SELECT numerator, denominator FROM gold.agg_data_quality "
    "WHERE metric = 'target_roles_analyzed'"
)
t_done = int(target["numerator"].iloc[0]) if len(target) else 0
t_all = int(target["denominator"].iloc[0]) if len(target) else 0

skills = load(
    "SELECT d.canonical_name AS skill, count(*) AS n FROM gold.bridge_posting_skill b "
    "JOIN gold.fct_postings p ON p.posting_key = b.posting_key "
    "JOIN gold.dim_skill d ON d.skill_id = b.skill_id "
    "WHERE p.is_active AND p.has_extraction AND NOT p.is_probable_duplicate "
    "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT ?",
    [cfg.home.top_skills],
)
pay = load(
    "SELECT median((coalesce(salary_min_annual, salary_max_annual) + "
    "coalesce(salary_max_annual, salary_min_annual)) / 2) AS median_pay, count(*) AS n "
    "FROM gold.fct_postings WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
    "AND salary_currency = 'USD' AND coalesce(salary_min_annual, salary_max_annual) IS NOT NULL"
).iloc[0]
analyzed = int(
    load(
        "SELECT count(*) AS n FROM gold.fct_postings "
        "WHERE is_active AND has_extraction AND NOT is_probable_duplicate"
    )["n"].iloc[0]
)
visa = load(
    "SELECT name, n_lca_certified FROM gold.dim_company ORDER BY n_lca_certified DESC, name"
)
with_filings = int((visa["n_lca_certified"] > 0).sum())

c1, c2, c3, c4 = st.columns(4)
with c1, st.container(border=True):
    st.metric("Open postings", f"{int(counts['active']):,}", delta=open_delta, delta_color="off")
    st.caption(
        f"Across {int(counts['companies'])} companies. {t_done:,} of {t_all:,} open data and AI "
        f"roles ({pct(t_done, t_all)}) are analyzed in depth."
    )
    st.page_link("app_pages/market_overview.py", label="Market overview", icon=":material/public:")
with c2, st.container(border=True):
    if len(skills):
        top = skills.iloc[0]
        st.metric("Most requested skill", str(top["skill"]))
        st.caption(
            f"In {pct(top['n'], analyzed)} of {analyzed:,} analyzed postings. Next: "
            + ", ".join(skills["skill"].iloc[1:].tolist())
            + "."
        )
    else:
        st.metric("Most requested skill", "n/a")
        st.caption("No analyzed postings yet.")
    st.page_link("app_pages/hiring_trends.py", label="Hiring trends", icon=":material/trending_up:")
with c3, st.container(border=True):
    if pay["n"]:
        st.metric("Median advertised pay", f"${float(pay['median_pay']) / 1000:,.0f}K")
        st.caption(
            f"Yearly USD midpoint over {int(pay['n'])} of {analyzed:,} analyzed postings that "
            "state a salary."
        )
    else:
        st.metric("Median advertised pay", "n/a")
        st.caption("No advertised salaries yet.")
    st.page_link("app_pages/salary.py", label="Salary", icon=":material/payments:")
with c4, st.container(border=True):
    st.metric("Companies with H-1B filings", f"{with_filings} of {len(visa)}")
    if with_filings:
        lead = visa.iloc[0]
        st.caption(
            f"Most certified filings: {lead['name']} ({int(lead['n_lca_certified']):,}). "
            "Evidence, not a promise."
        )
    st.page_link("app_pages/visa.py", label="Visa intelligence", icon=":material/flight_takeoff:")

# ---- where to go next ------------------------------------------------------------------------
st.subheader("Try it with your resume")
with st.container(border=True):
    st.markdown(
        "Paste your resume and see which open data and AI postings match your skills, what is "
        "missing, whether the company has H-1B filing history, and the advertised pay. Nothing "
        "is stored or logged, and no AI service is called."
    )
    st.page_link(
        "app_pages/resume_match.py", label="Open Resume match", icon=":material/description:"
    )
st.page_link(
    "app_pages/pipeline_health.py",
    label="Pipeline health: how fresh and complete the data is",
    icon=":material/health_and_safety:",
)
visa_disclaimer()
