import streamlit as st

from jobpulse.app_ui import bar, load, visa_disclaimer

visa_disclaimer()
st.caption(
    "Public filings: US Department of Labor H-1B labor condition applications, fiscal years in "
    "the data below. Only plain 'certified' filings count as evidence; withdrawn and denied "
    "filings are shown separately."
)

companies = load(
    "SELECT company_id, name, visa_evidence_class, n_lca_certified, n_lca_not_certified, "
    "lca_match_confidence, lca_employer_names, lca_match_methods, n_active, "
    "n_active_offered, n_active_not_offered FROM gold.dim_company ORDER BY n_lca_certified DESC"
)
if companies.empty:
    st.warning("No company data yet.")
    st.stop()

LABELS = {
    "HISTORICAL_EVIDENCE": "Has certified filings",
    "UNKNOWN": "No filings found",
}
companies["Evidence"] = companies["visa_evidence_class"].map(LABELS)
st.subheader("Company filing history")
st.caption(
    "Always read the class together with the number of certified filings: three filings is thin "
    "evidence, hundreds is not."
)
st.dataframe(
    companies[
        [
            "name",
            "Evidence",
            "n_lca_certified",
            "n_lca_not_certified",
            "lca_match_confidence",
            "n_active",
            "n_active_offered",
            "n_active_not_offered",
        ]
    ],
    hide_index=True,
    column_config={
        "name": "Company",
        "n_lca_certified": st.column_config.NumberColumn("Certified filings"),
        "n_lca_not_certified": st.column_config.NumberColumn(
            "Withdrawn or denied", help="Not counted as evidence."
        ),
        "lca_match_confidence": st.column_config.NumberColumn(
            "Name-match confidence", format="%.1f"
        ),
        "n_active": "Open postings",
        "n_active_offered": st.column_config.NumberColumn(
            "Postings offering sponsorship", help="Postings that say so in their text."
        ),
        "n_active_not_offered": st.column_config.NumberColumn(
            "Postings not offering", help="Postings that say sponsorship is not available."
        ),
    },
    alt="Visa filing history and posting sponsorship statements per company",
)

st.subheader("One company")
name = st.selectbox("Company", companies["name"].tolist(), key="visa_company")
row = companies[companies["name"] == name].iloc[0]
cid = row["company_id"]
with st.container(horizontal=True):
    st.metric("Certified filings", f"{int(row['n_lca_certified']):,}", border=True)
    st.metric("Withdrawn or denied", f"{int(row['n_lca_not_certified']):,}", border=True)
    st.metric("Evidence", LABELS[row["visa_evidence_class"]], border=True)
names = row["lca_employer_names"]
methods = row["lca_match_methods"]
if hasattr(names, "__len__") and len(names):
    st.caption(
        f"Matched filing name(s): {', '.join(names)} "
        f"({', '.join(methods)}, confidence {row['lca_match_confidence']:.1f})."
    )
else:
    st.caption("No H-1B filings found in the loaded fiscal years.")

by_year = load(
    "SELECT fiscal_year, n_certified, n_certified_withdrawn, n_withdrawn, n_denied "
    "FROM gold.agg_lca_company_year WHERE company_id = ? ORDER BY fiscal_year",
    [cid],
)
if not by_year.empty:
    st.dataframe(
        by_year,
        hide_index=True,
        column_config={
            "fiscal_year": st.column_config.NumberColumn("Fiscal year", format="%d"),
            "n_certified": "Certified",
            "n_certified_withdrawn": "Certified, then withdrawn",
            "n_withdrawn": "Withdrawn",
            "n_denied": "Denied",
        },
        alt="H-1B filings by fiscal year and outcome",
    )
    left, right = st.columns(2)
    with left:
        st.markdown("**Certified filings by role family (lower bounds)**")
        roles = load(
            "SELECT role_family, sum(n_certified) AS certified FROM gold.agg_lca_company_role "
            "WHERE company_id = ? GROUP BY 1 ORDER BY 2 DESC",
            [cid],
        )
        bar(roles, x="role_family", y="certified", horizontal=True, sort="-certified")
        st.caption("Role family is a job-title keyword match; some data roles are under 'other'.")
    with right:
        st.markdown("**Prevailing-wage level (I lowest, IV highest)**")
        levels = load(
            "SELECT wage_level, sum(n_certified) AS certified FROM gold.agg_lca_wage_level "
            "WHERE company_id = ? GROUP BY 1 ORDER BY 1",
            [cid],
        )
        bar(levels, x="wage_level", y="certified")

st.subheader("What postings say")
st.caption(
    "A posting's own words take precedence over filing history for that posting. Postings we have "
    "not read yet fall back to the company's filings and show no quote."
)
posts = load(
    "SELECT title, sponsorship_class, sponsorship_quote, city FROM gold.fct_postings "
    "WHERE company_id = ? AND is_active AND sponsorship_quote IS NOT NULL ORDER BY title",
    [cid],
)
if posts.empty:
    st.info("No open posting of this company states a sponsorship position (among those read).")
else:
    for _, p in posts.iterrows():
        with st.container(border=True):
            verdict = (
                "Sponsorship offered"
                if p["sponsorship_class"] == "CONFIRMED_OFFERED"
                else "Sponsorship not available"
            )
            st.markdown(f"**{p['title']}** ({p['city'] or 'location unspecified'}): {verdict}")
            st.markdown(f"> {p['sponsorship_quote']}")
