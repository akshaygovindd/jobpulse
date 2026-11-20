import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    DATA_ROLES,
    MATCH_LABELS,
    ROLE_LABELS,
    bar,
    human,
    key_insight,
    load,
    role_label,
    visa_disclaimer,
)

visa_disclaimer()
st.caption(
    "Public filings: US Department of Labor H-1B labor condition applications for fiscal years "
    "2025 and 2026. Only plain 'certified' filings count as evidence; withdrawn and denied "
    "filings are shown separately."
)

companies = load(
    "SELECT company_id, name, visa_evidence_class, n_lca_certified, n_lca_not_certified, "
    "lca_match_confidence, lca_employer_names, lca_match_methods, lca_subsidiaries, "
    "n_active "
    "FROM gold.dim_company ORDER BY n_lca_certified DESC"
)
if companies.empty:
    st.warning("No company data yet.")
    st.stop()

EVIDENCE = {"HISTORICAL_EVIDENCE": "Has certified filings", "UNKNOWN": "No filings found"}


def match_words(methods: object) -> str:
    if not hasattr(methods, "__len__") or not len(methods):  # type: ignore[arg-type]
        return "No match"
    return ", ".join(human(m, MATCH_LABELS) for m in methods)  # type: ignore[attr-defined]


companies["Evidence"] = companies["visa_evidence_class"].map(EVIDENCE)
companies["How the filings were matched"] = companies["lca_match_methods"].map(match_words)

st.subheader("Top sponsors by certified filings")
picked = st.multiselect(
    "Role families",
    list(ROLE_LABELS),
    default=DATA_ROLES,
    format_func=role_label,
    help="Certified filings are assigned a role family from the job title on the filing. This "
    "is a keyword match, so the counts are lower bounds. Remove all data roles and pick 'Other "
    "roles' to see the rest.",
    key="visa_roles",
)
if picked:
    marks = ", ".join("?" for _ in picked)
    sponsors = load(
        "SELECT c.name AS company, sum(r.n_certified) AS filings FROM gold.agg_lca_company_role r "
        f"JOIN gold.dim_company c USING (company_id) WHERE r.role_family IN ({marks}) "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 15",
        picked,
    )
    key_insight(
        insights.sponsors_insight(list(zip(sponsors["company"], sponsors["filings"], strict=True)))
    )
    bar(sponsors, x="company", y="filings", horizontal=True, sort="-filings")
    st.caption(
        "Certified H-1B filings in fiscal years 2025 and 2026 for the selected roles. A filing "
        "shows an employer applied for a role of that kind, not that a particular posting is "
        "sponsored."
    )
else:
    st.info("Pick at least one role family.")

st.subheader("All companies")
st.caption(
    "Read the evidence together with the number of certified filings: three filings is thin "
    "evidence, hundreds is not."
)
st.dataframe(
    companies[
        [
            "name",
            "Evidence",
            "n_lca_certified",
            "n_lca_not_certified",
            "How the filings were matched",
            "n_active",
        ]
    ],
    hide_index=True,
    column_config={
        "name": st.column_config.TextColumn("Company", width="medium"),
        "Evidence": st.column_config.TextColumn(width="medium"),
        "How the filings were matched": st.column_config.TextColumn(width="large"),
        "n_lca_certified": st.column_config.NumberColumn("Certified filings", format="%d"),
        "n_lca_not_certified": st.column_config.NumberColumn(
            "Withdrawn or denied", format="%d", help="Not counted as evidence."
        ),
        "n_active": st.column_config.NumberColumn("Open postings", format="%d"),
    },
    alt="Visa filing history per company",
)

st.subheader("One company")
name = st.selectbox("Company", companies["name"].tolist(), key="visa_company")
row = companies[companies["name"] == name].iloc[0]
cid = row["company_id"]
with st.container(horizontal=True):
    st.metric("Certified filings", f"{int(row['n_lca_certified']):,}", border=True)
    st.metric("Withdrawn or denied", f"{int(row['n_lca_not_certified']):,}", border=True)
    st.metric("Evidence", EVIDENCE[row["visa_evidence_class"]], border=True)
names = row["lca_employer_names"]
if hasattr(names, "__len__") and len(names):
    matched_by = row["How the filings were matched"]
    st.caption(f"Filed under: {', '.join(names)}. Matched by: {matched_by}.")
    subs = row["lca_subsidiaries"]
    if hasattr(subs, "__len__") and len(subs):
        st.caption(
            f"Includes filings by subsidiaries (reviewed): {', '.join(subs)}. "
            "They are counted in the certified filings above."
        )
else:
    st.caption("No H-1B filings found in fiscal years 2025 and 2026.")

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
            "n_certified": st.column_config.NumberColumn("Certified", format="%d"),
            "n_certified_withdrawn": st.column_config.NumberColumn(
                "Certified, then withdrawn", format="%d"
            ),
            "n_withdrawn": st.column_config.NumberColumn("Withdrawn", format="%d"),
            "n_denied": st.column_config.NumberColumn("Denied", format="%d"),
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
        roles["role"] = roles["role_family"].map(role_label)
        bar(roles, x="role", y="certified", horizontal=True, sort="-certified")
        st.caption(
            "Role family is a job-title keyword match; some data roles are under 'Other roles'."
        )
    with right:
        st.markdown("**Prevailing-wage level (I lowest, IV highest)**")
        levels = load(
            "SELECT wage_level, sum(n_certified) AS certified FROM gold.agg_lca_wage_level "
            "WHERE company_id = ? GROUP BY 1 ORDER BY 1",
            [cid],
        )
        levels["level"] = levels["wage_level"].map(
            lambda v: "Not stated" if v == "unknown" else f"Level {v}"
        )
        bar(levels, x="level", y="certified")

st.subheader("What this company's postings say")
stated = load(
    "SELECT count(*) FILTER (WHERE has_extraction) AS analyzed, "
    "count(*) FILTER (WHERE sponsorship_class = 'CONFIRMED_OFFERED') AS offering, "
    "count(*) FILTER (WHERE sponsorship_class = 'EXPLICITLY_UNAVAILABLE') AS not_offering "
    "FROM gold.fct_postings WHERE company_id = ? AND is_active",
    [cid],
).iloc[0]
if not stated["analyzed"]:
    st.info(
        "Not stated: none of this company's open postings has been analyzed yet, so we do not "
        "know what they say about sponsorship."
    )
else:
    silent = int(stated["analyzed"] - stated["offering"] - stated["not_offering"])
    st.caption(
        f"Of {int(stated['analyzed'])} analyzed open postings: {int(stated['offering'])} say "
        f"sponsorship is offered, {int(stated['not_offering'])} say it is not available, and "
        f"{silent} do not say. Postings that were not analyzed are not counted. A posting's own "
        "words take precedence over filing history for that posting."
    )
posts = load(
    "SELECT title, sponsorship_class, sponsorship_quote, city FROM gold.fct_postings "
    "WHERE company_id = ? AND is_active AND sponsorship_quote IS NOT NULL ORDER BY title",
    [cid],
)
for _, p in posts.iterrows():
    with st.container(border=True):
        verdict = (
            "Sponsorship offered"
            if p["sponsorship_class"] == "CONFIRMED_OFFERED"
            else "Sponsorship not available"
        )
        st.markdown(f"**{p['title']}** ({p['city'] or 'location not stated'}): {verdict}")
        st.markdown(f"> {p['sponsorship_quote']}")
