import streamlit as st

from jobpulse.app_ui import bar, load, pct

counts = load(
    "SELECT count(*) AS n, count(*) FILTER (WHERE is_active) AS active, "
    "count(*) FILTER (WHERE is_active AND has_extraction) AS extracted, "
    "count(DISTINCT company_id) FILTER (WHERE is_active) AS companies, "
    "count(DISTINCT city) FILTER (WHERE is_active) AS cities "
    "FROM gold.fct_postings WHERE NOT is_probable_duplicate"
).iloc[0]

if not counts["n"]:
    st.warning("No postings yet. Run the pipeline, then `jobpulse build-gold`.")
    st.stop()

with st.container(horizontal=True):
    st.metric("Open postings", f"{int(counts['active']):,}", border=True)
    st.metric("Companies hiring", f"{int(counts['companies']):,}", border=True)
    st.metric("Cities", f"{int(counts['cities']):,}", border=True)
    st.metric(
        "Read by the AI extractor",
        pct(counts["extracted"], counts["active"]),
        help="Role family, seniority, salary and skills come only from postings the extractor "
        "has read. The rest are counted here but not in the breakdowns below.",
        border=True,
    )

st.caption(
    f"Counts exclude probable duplicates. {int(counts['extracted']):,} of "
    f"{int(counts['active']):,} open postings have been extracted; role-family, remote and "
    "seniority charts cover only those."
)

left, right = st.columns(2)
with left:
    st.subheader("Open postings by company")
    by_company = load(
        "SELECT name, n_active FROM gold.dim_company WHERE n_active > 0 "
        "ORDER BY n_active DESC LIMIT 15"
    )
    bar(by_company, x="name", y="n_active", horizontal=True, sort="-n_active")
with right:
    st.subheader("Role family (extracted postings)")
    by_role = load(
        "SELECT role_family, count(*) AS postings FROM gold.fct_postings "
        "WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    bar(by_role, x="role_family", y="postings", horizontal=True, sort="-postings")

left, right = st.columns(2)
with left:
    st.subheader("Remote policy (extracted postings)")
    remote = load(
        "SELECT remote_policy, count(*) AS postings FROM gold.fct_postings "
        "WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    bar(remote, x="remote_policy", y="postings", horizontal=True, sort="-postings")
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
            f"A city could be read for {pct(row['numerator'], row['denominator'])} of open "
            "postings; the rest are remote-only, multi-location or unparsed."
        )
