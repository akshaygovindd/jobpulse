import streamlit as st

from jobpulse.app_ui import (
    REMOTE_LABELS,
    bar,
    human,
    load,
    pct,
    role_label,
)

GITHUB_URL = "https://github.com/akshaygovindd/jobpulse"

st.markdown(
    "**Daily job-market intelligence from 43 tech employers' public job boards.** "
    "Open roles, hiring trends, advertised pay and public H-1B filing evidence for data and AI "
    "jobs."
)
st.link_button("View the project on GitHub", GITHUB_URL, icon=":material/code:")

counts = load(
    "SELECT count(*) AS n, count(*) FILTER (WHERE is_active) AS active, "
    "count(*) FILTER (WHERE is_active AND has_extraction) AS analyzed, "
    "count(DISTINCT company_id) FILTER (WHERE is_active) AS companies, "
    "count(DISTINCT city) FILTER (WHERE is_active) AS cities "
    "FROM gold.fct_postings WHERE NOT is_probable_duplicate"
).iloc[0]

if not counts["n"]:
    st.warning("No postings yet. Run the pipeline, then `jobpulse build-gold`.")
    st.stop()

target = load(
    "SELECT numerator, denominator FROM gold.agg_data_quality "
    "WHERE metric = 'target_roles_analyzed'"
)
t_done = int(target["numerator"].iloc[0]) if len(target) else int(counts["analyzed"])
t_all = int(target["denominator"].iloc[0]) if len(target) else 0

with st.container(horizontal=True):
    st.metric("Open postings", f"{int(counts['active']):,}", border=True)
    st.metric("Companies hiring", f"{int(counts['companies']):,}", border=True)
    st.metric("Cities", f"{int(counts['cities']):,}", border=True)
    st.metric(
        "Data & AI roles analyzed",
        f"{t_done:,}",
        help="The AI reads only postings whose titles look like data and AI roles (a title "
        "filter), to keep cost low. Role family, seniority, salary, skills and sponsorship "
        "wording come from those postings.",
        border=True,
    )

st.caption(
    f"The AI reads only data and AI roles (chosen by job title). Of {t_all:,} open postings in "
    f"those roles, {t_done:,} ({pct(t_done, t_all)}) have been analyzed so far. The other "
    f"{int(counts['active']) - t_all:,} open postings are counted here but not read by the AI, so "
    "the role, remote and pay breakdowns below cover analyzed postings only. Duplicates excluded."
)

left, right = st.columns(2)
with left:
    st.subheader("Open postings by company")
    by_company = load(
        "SELECT name AS company, n_active AS postings FROM gold.dim_company WHERE n_active > 0 "
        "ORDER BY n_active DESC LIMIT 15"
    )
    bar(by_company, x="company", y="postings", horizontal=True, sort="-postings")
with right:
    st.subheader("Role family (analyzed postings)")
    by_role = load(
        "SELECT role_family, count(*) AS postings FROM gold.fct_postings "
        "WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    by_role["role"] = by_role["role_family"].map(role_label)
    bar(by_role, x="role", y="postings", horizontal=True, sort="-postings")

left, right = st.columns(2)
with left:
    st.subheader("Remote policy (analyzed postings)")
    remote = load(
        "SELECT remote_policy, count(*) AS postings FROM gold.fct_postings "
        "WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    remote["policy"] = remote["remote_policy"].map(lambda v: human(v, REMOTE_LABELS))
    bar(remote, x="policy", y="postings", horizontal=True, sort="-postings")
with right:
    st.subheader("Top cities")
    cities = load(
        "SELECT city, count(*) AS postings FROM gold.fct_postings "
        "WHERE is_active AND NOT is_probable_duplicate AND city IS NOT NULL "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
    )
    bar(cities, x="city", y="postings", horizontal=True, sort="-postings")
    quality = load(
        "SELECT numerator, denominator FROM gold.agg_data_quality WHERE metric = 'city_parsed'"
    )
    if len(quality):
        row = quality.iloc[0]
        st.caption(
            f"A single city could be read for {pct(row['numerator'], row['denominator'])} of "
            "open postings; the rest are remote-only or list several locations."
        )
