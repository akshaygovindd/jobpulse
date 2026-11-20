import streamlit as st

from jobpulse.app_ui import load, pct, visa_disclaimer

st.caption(
    "Two separate datasets that are never blended: salaries advertised in postings, and wages "
    "employers stated on H-1B labor condition applications (LCAs)."
)
advertised_tab, lca_tab = st.tabs(["Advertised salary", "LCA offered wages"])

with advertised_tab:
    cov = load(
        "SELECT sum(numerator) FILTER (WHERE metric = 'advertised_salary') AS with_salary, "
        "sum(denominator) FILTER (WHERE metric = 'advertised_salary') AS extracted, "
        "sum(numerator) FILTER (WHERE metric = 'salary_annualizable') AS annualizable "
        "FROM gold.agg_data_quality"
    ).iloc[0]
    if not cov["extracted"]:
        st.info("No extracted postings yet.")
    else:
        with st.container(horizontal=True):
            st.metric(
                "Postings with an advertised salary",
                pct(cov["with_salary"], cov["extracted"]),
                help=f"{int(cov['with_salary'])} of {int(cov['extracted'])} extracted postings.",
                border=True,
            )
            st.metric(
                "Of those, annual figure known",
                pct(cov["annualizable"], cov["with_salary"]),
                help="Needs a stated or inferred pay period (hour, month, year...).",
                border=True,
            )
        currencies = load(
            "SELECT salary_currency, sum(n_currency_salary) AS n FROM gold.agg_salary_by_role_city "
            "WHERE salary_currency <> 'none' GROUP BY 1 ORDER BY 2 DESC"
        )
        if currencies.empty:
            st.info("No advertised salaries yet.")
        else:
            cur = st.segmented_control(
                "Currency (never converted or mixed)",
                currencies["salary_currency"].tolist(),
                default=currencies["salary_currency"].iloc[0],
                key="sal_currency",
            )
            if cur:
                table = load(
                    "SELECT role_family, seniority, city, n_postings, n_with_salary, coverage_pct, "
                    "n_annualized, p25_annual, p50_annual, p75_annual "
                    "FROM gold.agg_salary_by_role_city WHERE salary_currency = ? "
                    "ORDER BY n_annualized DESC, n_postings DESC",
                    [cur],
                )
                st.caption(
                    f"Annual salary in {cur} (midpoint of the advertised range). Percentiles "
                    "appear only where enough salaries exist; the coverage column is the share "
                    "of postings in that group that advertise any salary."
                )
                st.dataframe(
                    table,
                    hide_index=True,
                    column_config={
                        "coverage_pct": st.column_config.NumberColumn("Coverage %", format="%.0f"),
                        "p25_annual": st.column_config.NumberColumn("P25", format="localized"),
                        "p50_annual": st.column_config.NumberColumn("Median", format="localized"),
                        "p75_annual": st.column_config.NumberColumn("P75", format="localized"),
                    },
                    alt="Advertised annual salary percentiles by role family, seniority and city",
                )

with lca_tab:
    visa_disclaimer()
    st.caption(
        "Wages stated by employers on certified LCAs (lower bound of the filed range), for the "
        "43 companies we track only. Not a market survey and not comparable one-to-one with "
        "advertised salary. Wages that are implausible after annualizing (filer unit errors) are "
        "excluded, not corrected."
    )
    wages = load(
        "SELECT role_family, worksite_state, worksite_city, n_certified, n_with_wage, "
        "n_wage_excluded, coverage_pct, p25_annual, p50_annual, p75_annual "
        "FROM gold.agg_lca_wage_by_soc_city ORDER BY n_certified DESC"
    )
    if wages.empty:
        st.info("No LCA data loaded yet.")
    else:
        total = int(wages["n_certified"].sum())
        used = int(wages["n_with_wage"].sum())
        excluded = int(wages["n_wage_excluded"].sum())
        with st.container(horizontal=True):
            st.metric("Certified filings", f"{total:,}", border=True)
            st.metric("With a usable wage", pct(used, total), border=True)
            st.metric("Excluded as implausible", f"{excluded:,}", border=True)
        st.caption(
            "By-role counts are lower bounds: the role family is a job-title keyword match, so "
            "some data roles fall under 'other'. Percentiles need at least five wages."
        )
        st.dataframe(
            wages,
            hide_index=True,
            column_config={
                "coverage_pct": st.column_config.NumberColumn("Coverage %", format="%.0f"),
                "p25_annual": st.column_config.NumberColumn("P25", format="localized"),
                "p50_annual": st.column_config.NumberColumn("Median", format="localized"),
                "p75_annual": st.column_config.NumberColumn("P75", format="localized"),
            },
            alt="Offered wages on certified LCAs by role family and worksite",
        )
