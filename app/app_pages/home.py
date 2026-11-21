import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    app_config,
    kpi_basis,
    kpi_comparison,
    kpi_label,
    kpi_series,
    load,
    pct,
    skill_label,
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
    st.warning("No postings yet. The data is refreshed daily; please check back soon.")
    st.stop()

latest = load("SELECT max(newest_snapshot_date) AS d FROM gold.fct_snapshot_freshness")["d"].iloc[0]

# ---- hero ------------------------------------------------------------------------------------
with st.container(key="hero"):
    st.markdown("# JobPulse")
    st.markdown("### Daily job-market intelligence for data and AI roles")
    st.markdown(
        "What is open, what it pays, which skills employers ask for, and who has a public record "
        f"of H-1B filings, from the job boards of {int(counts['companies'])} tech companies."
    )
    with st.container(horizontal=True, vertical_alignment="center"):
        st.link_button("View the project on GitHub", GITHUB_URL, icon=":material/code:")
        st.page_link(
            "app_pages/resume_match.py", label="Match your resume", icon=":material/description:"
        )
        if latest is not None and latest == latest:
            st.caption(f":material/update: Data through {latest:%d %b %Y}, refreshed daily")

# ---- insight cards (all numbers computed from gold) ------------------------------------------
cfg = app_config()
days = cfg.kpi.delta_days
now_kpi, before_kpi = kpi_comparison(days, target_only=True)  # data and AI roles
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
# Interns are left out: their hourly or monthly pay annualized is not a yearly salary.
pay = load(
    "SELECT median((coalesce(salary_min_annual, salary_max_annual) + "
    "coalesce(salary_max_annual, salary_min_annual)) / 2) AS median_pay, "
    "count(*) FILTER (WHERE coalesce(salary_min_annual, salary_max_annual) IS NOT NULL "
    "AND seniority <> 'intern') AS n, count(*) AS n_usd "
    "FROM gold.fct_postings WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
    "AND salary_currency = 'USD' AND has_advertised_salary AND seniority <> 'intern'"
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
with c1, st.container(border=True, key="card_kpi_0"):
    trend = kpi_series(7, target_only=True)
    basis = kpi_basis(days)
    st.metric(
        "Open data & AI roles",
        f"{t_all:,}",
        delta=open_delta,
        delta_color="off",
        icon=":material/work:",
        chart_data=trend,
        chart_type="area",
        help=(
            f"Open postings whose job titles look like data and AI roles; the AI reads "
            f"{t_done:,} of them ({pct(t_done, t_all)}). All roles together: "
            f"{int(counts['active']):,} open postings. "
        )
        + (f"Sparkline: the last {len(trend)} settled days. " if trend else "")
        + (f"The change compares settled days: {basis}." if open_delta and basis else ""),
    )
    all_now, all_before = kpi_comparison(days)  # all roles, as on Market overview
    all_delta = insights.delta_text(
        all_now["postings"],
        None if all_before is None else all_before["postings"],
        unit=kpi_label(days),
    )
    st.caption(
        f"of {int(counts['active']):,} open postings ({all_delta + '; ' if all_delta else ''}all "
        f"roles) across {int(counts['companies'])} companies"
    )
    st.page_link("app_pages/market_overview.py", label="Market overview", icon=":material/public:")
with c2, st.container(border=True, key="card_kpi_1"):
    if len(skills):
        top = skills.iloc[0]
        st.metric(
            "Most requested skill",
            skill_label(str(top["skill"])),
            icon=":material/psychology:",
            help=f"In {pct(top['n'], analyzed)} of {analyzed:,} analyzed postings.",
        )
        st.caption(
            f"In {pct(top['n'], analyzed)} of postings. Next: "
            + ", ".join(skill_label(v) for v in skills["skill"].iloc[1:])
        )
    else:
        st.metric("Most requested skill", "n/a", icon=":material/psychology:")
        st.caption("No analyzed postings yet.")
    st.page_link("app_pages/hiring_trends.py", label="Hiring trends", icon=":material/trending_up:")
with c3, st.container(border=True, key="card_kpi_2"):
    if pay["n"]:
        st.metric(
            "Median advertised pay",
            f"${float(pay['median_pay']) / 1000:,.0f}K",
            icon=":material/payments:",
            help=f"Midpoint of the advertised USD range over {int(pay['n'])} of the "
            f"{int(pay['n_usd'])} non-intern postings with a USD salary (of {analyzed:,} "
            "analyzed). Hourly and monthly pay is converted to a yearly equivalent. Internships "
            "are left out.",
        )
        st.caption(f"Advertised yearly pay, USD, {int(pay['n'])} postings")
    else:
        st.metric("Median advertised pay", "n/a", icon=":material/payments:")
        st.caption("No advertised salaries yet.")
    st.page_link("app_pages/salary.py", label="Salary", icon=":material/payments:")
with c4, st.container(border=True, key="card_kpi_3"):
    st.metric(
        "Companies with H-1B filings",
        f"{with_filings} of {len(visa)}",
        icon=":material/flight_takeoff:",
        help="Evidence from public filings, not a promise of sponsorship.",
    )
    if with_filings:
        lead = visa.iloc[0]
        st.caption(f"Most filings: {lead['name']} ({int(lead['n_lca_certified']):,})")
    st.page_link("app_pages/visa.py", label="Visa intelligence", icon=":material/flight_takeoff:")

# ---- where to go next ------------------------------------------------------------------------
st.subheader("Try it with your resume")
with st.container(border=True):
    st.markdown(
        "Paste your resume to see which open data and AI postings match your skills, what is "
        "missing, H-1B filing history and pay. Nothing is stored or logged, and no AI service "
        "is called."
    )
    st.page_link(
        "app_pages/resume_match.py", label="Open Resume match", icon=":material/description:"
    )
st.page_link(
    "app_pages/pipeline_health.py",
    label="Pipeline health",
    icon=":material/health_and_safety:",
    help="How fresh and complete the data is",
)
visa_disclaimer()
