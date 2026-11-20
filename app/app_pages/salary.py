import streamlit as st

from jobpulse.app_ui import (
    SENIORITY_LABELS,
    human,
    load,
    pct,
    role_label,
    salary_chart,
    usd_k,
    visa_disclaimer,
)

NO_CITY = "No single city (remote or multi-location)"
MONEY = "$%.0fK"

st.caption(
    "Two separate datasets that are never blended: salaries advertised in postings, and wages "
    "employers stated on H-1B labor condition applications (LCAs)."
)
advertised_tab, lca_tab = st.tabs(["Advertised salary", "LCA offered wages"])

with advertised_tab:
    cov = load(
        "SELECT sum(numerator) FILTER (WHERE metric = 'advertised_salary') AS with_salary, "
        "sum(denominator) FILTER (WHERE metric = 'advertised_salary') AS analyzed, "
        "sum(numerator) FILTER (WHERE metric = 'salary_annualizable') AS annualizable "
        "FROM gold.agg_data_quality"
    ).iloc[0]
    if not cov["analyzed"]:
        st.info("No analyzed postings yet.")
    else:
        with st.container(horizontal=True):
            st.metric(
                "Postings with an advertised salary",
                pct(cov["with_salary"], cov["analyzed"]),
                help=f"{int(cov['with_salary'])} of {int(cov['analyzed'])} analyzed postings.",
                border=True,
            )
            st.metric(
                "Of those, yearly figure known",
                pct(cov["annualizable"], cov["with_salary"]),
                help="Needs a stated or inferred pay period (hour, month, year...).",
                border=True,
            )
        currencies = load(
            "SELECT salary_currency, sum(n_currency_salary) AS n FROM gold.agg_salary_by_role "
            "WHERE salary_currency <> 'none' GROUP BY 1 ORDER BY 2 DESC"
        )
        if currencies.empty:
            st.info("No advertised salaries yet.")
            st.stop()
        left, right = st.columns(2)
        with left:
            cur = st.segmented_control(
                "Currency (never converted or mixed)",
                currencies["salary_currency"].tolist(),
                default=currencies["salary_currency"].iloc[0],
                key="sal_currency",
            )
        city_rows = load(
            "SELECT city, sum(n_annualized) AS n FROM gold.agg_salary_by_role_city "
            "WHERE salary_currency = ? GROUP BY 1 HAVING sum(n_annualized) > 0 ORDER BY 2 DESC",
            [cur or ""],
        )
        city_options = ["All cities", *city_rows["city"].tolist()]
        with right:
            city = st.selectbox(
                "City",
                city_options,
                format_func=lambda c: NO_CITY if c == "unspecified" else c,
                key="sal_city",
                help="Most advertised salaries are for remote or multi-location roles, which "
                "have no single city. 'All cities' includes them.",
            )
        if cur:
            if city == "All cities":
                table = load(
                    "SELECT role_family, seniority, n_postings, n_with_salary, coverage_pct, "
                    "n_annualized, p25_annual, p50_annual, p75_annual "
                    "FROM gold.agg_salary_by_role WHERE salary_currency = ? "
                    "ORDER BY n_annualized DESC, n_postings DESC",
                    [cur],
                )
            else:
                table = load(
                    "SELECT role_family, seniority, n_postings, n_with_salary, coverage_pct, "
                    "n_annualized, p25_annual, p50_annual, p75_annual "
                    "FROM gold.agg_salary_by_role_city WHERE salary_currency = ? AND city = ? "
                    "ORDER BY n_annualized DESC, n_postings DESC",
                    [cur, city],
                )
            table["Role family"] = table["role_family"].map(role_label)
            table["Seniority"] = table["seniority"].map(lambda v: human(v, SENIORITY_LABELS))
            for src, dst in (
                ("p25_annual", "P25"),
                ("p50_annual", "Median"),
                ("p75_annual", "P75"),
            ):
                table[dst] = table[src].map(usd_k)
            chart = table.dropna(subset=["Median"]).copy()
            st.subheader(f"Median advertised salary ({cur})")
            if chart.empty:
                st.info(
                    "Not enough salaries yet for a median (at least five per group are needed)."
                )
            else:
                salary_chart(chart)
                st.caption(
                    "Midpoint of the advertised range, converted to a yearly figure, for groups "
                    "with at least five salaries."
                )
            st.dataframe(
                table[
                    [
                        "Role family",
                        "Seniority",
                        "n_postings",
                        "n_with_salary",
                        "coverage_pct",
                        "n_annualized",
                        "P25",
                        "Median",
                        "P75",
                    ]
                ],
                hide_index=True,
                column_config={
                    "Role family": st.column_config.TextColumn(width="medium"),
                    "Seniority": st.column_config.TextColumn(width="small"),
                    "n_postings": st.column_config.NumberColumn("Postings", format="%d"),
                    "n_with_salary": st.column_config.NumberColumn("With a salary", format="%d"),
                    "coverage_pct": st.column_config.NumberColumn(
                        "Coverage", format="%.0f%%", help="Share of postings that state a salary."
                    ),
                    "n_annualized": st.column_config.NumberColumn(
                        "Yearly figure known", format="%d"
                    ),
                    "P25": st.column_config.NumberColumn(format=MONEY),
                    "Median": st.column_config.NumberColumn(format=MONEY),
                    "P75": st.column_config.NumberColumn(format=MONEY),
                },
                alt="Advertised yearly salary percentiles by role family and seniority",
            )
            st.caption("Percentiles appear only where at least five yearly salaries exist.")

with lca_tab:
    visa_disclaimer()
    st.caption(
        "Wages stated by employers on certified LCAs (lower bound of the filed range), for the "
        "43 companies we track only. Not a market survey and not comparable one-to-one with "
        "advertised salary. Wages that are implausible after converting to a yearly figure "
        "(filer unit errors) are excluded, not corrected."
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
            "some data roles fall under 'Other roles'. Percentiles need at least five wages."
        )
        wages["Role family"] = wages["role_family"].map(role_label)
        for src, dst in (("p25_annual", "P25"), ("p50_annual", "Median"), ("p75_annual", "P75")):
            wages[dst] = wages[src].map(usd_k)
        wages["State"] = wages["worksite_state"].replace("unspecified", "Not stated")
        wages["City"] = wages["worksite_city"].replace("unspecified", "Not stated")
        st.dataframe(
            wages[
                [
                    "Role family",
                    "State",
                    "City",
                    "n_certified",
                    "n_with_wage",
                    "n_wage_excluded",
                    "coverage_pct",
                    "P25",
                    "Median",
                    "P75",
                ]
            ],
            hide_index=True,
            column_config={
                "Role family": st.column_config.TextColumn(width="medium"),
                "State": st.column_config.TextColumn(width="small"),
                "City": st.column_config.TextColumn(width="medium"),
                "n_certified": st.column_config.NumberColumn("Certified filings", format="%d"),
                "n_with_wage": st.column_config.NumberColumn("With a usable wage", format="%d"),
                "n_wage_excluded": st.column_config.NumberColumn(
                    "Excluded as implausible", format="%d"
                ),
                "coverage_pct": st.column_config.NumberColumn("Coverage", format="%.0f%%"),
                "P25": st.column_config.NumberColumn(format=MONEY),
                "Median": st.column_config.NumberColumn(format=MONEY),
                "P75": st.column_config.NumberColumn(format=MONEY),
            },
            alt="Offered wages on certified LCAs by role family and worksite",
        )
