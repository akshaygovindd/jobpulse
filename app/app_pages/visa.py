import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    DATA_ROLES,
    MATCH_LABELS,
    ROLE_LABELS,
    bar,
    clean,
    human,
    is_missing,
    key_insight,
    load,
    pct,
    role_label,
    visa_disclaimer,
)

visa_disclaimer()
years = load("SELECT min(fiscal_year) AS lo, max(fiscal_year) AS hi FROM gold.agg_lca_company_year")
lo_fy, hi_fy = years["lo"].iloc[0], years["hi"].iloc[0]
if is_missing(lo_fy) or lo_fy == hi_fy:
    FY = "the loaded fiscal years"
else:
    FY = f"fiscal years {int(lo_fy)} and {int(hi_fy)}"
st.caption(
    f"Public filings: US Department of Labor H-1B labor condition applications for {FY}. Only "
    "plain 'certified' filings count as evidence; withdrawn and denied filings are shown "
    "separately. 'No matching filings found' can also mean the employer files under a name we "
    "could not match."
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

EVIDENCE = {"HISTORICAL_EVIDENCE": "Has certified filings", "UNKNOWN": "No matching filings found"}


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
    everyone = load(
        "SELECT sum(n_certified) AS filings FROM gold.agg_lca_company_role "
        f"WHERE role_family IN ({marks})",
        picked,
    )["filings"].iloc[0]
    all_roles = load("SELECT sum(n_certified) AS filings FROM gold.agg_lca_company_role")[
        "filings"
    ].iloc[0]
    everyone = 0.0 if is_missing(everyone) else float(everyone)
    all_roles = 0.0 if is_missing(all_roles) else float(all_roles)
    key_insight(
        insights.sponsors_insight(
            list(zip(sponsors["company"], sponsors["filings"], strict=True)),
            total=everyone or None,
        )
    )
    bar(sponsors, x="company", y="filings", horizontal=True, sort="-filings")
    st.caption(
        f"Certified H-1B filings in {FY} for the selected roles. A filing shows an employer "
        "applied for a role of that kind, not that a particular posting is sponsored. The "
        f"selected roles are {pct(everyone, all_roles)} of all certified filings of these "
        "companies; the rest are in other roles, so this list shows the minority."
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
            "Withdrawn or denied",
            format="%d",
            help="Not counted as evidence. Includes filings certified and then withdrawn.",
        ),
        "n_active": st.column_config.NumberColumn("Open postings", format="%d"),
    },
    alt="Visa filing history per company",
)

st.subheader("Company lookup")
name = st.selectbox(
    "Search for a company",
    companies["name"].tolist(),
    key="visa_company",
    help="Type to search. Ordered by certified filings, most first.",
)
row = companies[companies["name"] == name].iloc[0]
cid = row["company_id"]
by_year = load(
    "SELECT fiscal_year, n_certified, n_certified_withdrawn, n_withdrawn, n_denied "
    "FROM gold.agg_lca_company_year WHERE company_id = ? ORDER BY fiscal_year",
    [cid],
)
roles = load(
    "SELECT role_family, sum(n_certified) AS certified FROM gold.agg_lca_company_role "
    "WHERE company_id = ? GROUP BY 1 ORDER BY 2 DESC",
    [cid],
)
roles["role"] = roles["role_family"].map(role_label)
levels = load(
    "SELECT wage_level, sum(n_certified) AS certified FROM gold.agg_lca_wage_level "
    "WHERE company_id = ? GROUP BY 1 ORDER BY 1",
    [cid],
)
levels["level"] = levels["wage_level"].map(
    lambda v: "Not stated" if v == "unknown" else f"Level {v}"
)
named_roles = roles[roles["role_family"] != "other"]
named_levels = levels[levels["wage_level"] != "unknown"]
with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"### {name}")
        badge_color = "blue" if row["visa_evidence_class"] == "HISTORICAL_EVIDENCE" else "gray"
        st.markdown(f":{badge_color}-badge[{EVIDENCE[row['visa_evidence_class']]}]")
    with st.container(horizontal=True):
        st.metric("Certified filings", f"{int(row['n_lca_certified']):,}", border=True)
        st.metric("Withdrawn or denied", f"{int(row['n_lca_not_certified']):,}", border=True)
        st.metric("Open postings", f"{int(row['n_active']):,}", border=True)
        for _, y in by_year.iterrows():
            st.metric(
                f"FY{int(y['fiscal_year'])} certified", f"{int(y['n_certified']):,}", border=True
            )
    key_insight(
        insights.company_card_insight(
            [
                (int(y), float(n))
                for y, n in zip(by_year["fiscal_year"], by_year["n_certified"], strict=True)
            ],
            (str(named_roles["role"].iloc[0]), float(named_roles["certified"].iloc[0]))
            if len(named_roles)
            else None,
            (
                str(named_levels.sort_values("certified", ascending=False)["level"].iloc[0]),
                float(named_levels["certified"].max()),
            )
            if len(named_levels)
            else None,
        )
    )
    names = row["lca_employer_names"]
    if hasattr(names, "__len__") and len(names):
        matched_by = row["How the filings were matched"]
        filed_as = ", ".join(n.rstrip(".") for n in names)
        st.caption(f"Filed under: {filed_as}. Matched by: {matched_by}.")
        subs = row["lca_subsidiaries"]
        if hasattr(subs, "__len__") and len(subs):
            st.caption(
                f"Includes filings by subsidiaries (reviewed): {', '.join(subs)}. "
                "They are counted in the certified filings above."
            )
    else:
        st.caption(f"No matching H-1B filings found in {FY}.")
    st.caption(
        "Evidence from public filings, not a promise of sponsorship. Read the number of filings "
        "with the class: a handful is thin evidence."
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
        bar(roles, x="role", y="certified", horizontal=True, sort="-certified")
        st.caption(
            "Role family is a job-title keyword match; some data roles are under 'Other roles'."
        )
    with right:
        st.markdown("**Prevailing-wage level (I lowest, IV highest)**")
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
        st.markdown(f"**{p['title']}** ({clean(p['city'], 'location not stated')}): {verdict}")
        st.markdown(f"> {p['sponsorship_quote']}")
