import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    DATA_ROLES,
    SENIORITY_LABELS,
    app_config,
    bar,
    dash,
    human,
    k_text,
    key_insight,
    load,
    pct,
    role_label,
    salary_chart,
    usd_k,
    visa_disclaimer,
)

NO_CITY = "No single city (remote or multi-location)"

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
            table = table[table["seniority"] != "intern"]  # internship pay: its own table below
            table["Role family"] = table["role_family"].map(role_label)
            table["Seniority"] = table["seniority"].map(lambda v: human(v, SENIORITY_LABELS))
            for src, dst in (
                ("p25_annual", "P25"),
                ("p50_annual", "Median"),
                ("p75_annual", "P75"),
            ):
                table[dst] = table[src].map(usd_k)
            sign = "$" if cur == "USD" else f"{cur} "
            chart = table.dropna(subset=["Median"]).copy()  # the groups with five or more salaries
            st.subheader(f"Median advertised salary ({cur})")
            if chart.empty:
                n_thin = int(table["n_annualized"].sum())
                st.info(
                    f"No median for {cur} yet: {n_thin:,} yearly "
                    f"salar{'y' if n_thin == 1 else 'ies'} across {len(table)} "
                    f"group{'' if len(table) == 1 else 's'}, and a median needs at least five in "
                    "one group (role family and seniority). The table below lists the groups.",
                    icon=":material/info:",
                )
            else:
                # The sentence is computed from exactly the rows the chart draws below.
                key_insight(
                    insights.salary_insight(
                        [
                            (
                                f"{r['Seniority']} {r['Role family']}",
                                r["Median"],
                                int(r["n_annualized"]),
                            )
                            for _, r in chart.iterrows()
                        ],
                        small_below=app_config().samples.min_group_n,
                        sign=sign,
                    )
                )
                salary_chart(chart, sign=sign)
                st.caption(
                    "Midpoint of the advertised range, converted to a yearly figure, for groups "
                    "with at least five salaries; a blank cell means fewer than five. Hourly and "
                    "monthly pay is converted to a yearly equivalent. Internships are left out "
                    "(their pay is not a yearly salary); see the internship table below."
                )
            shown = table.copy()
            for col in ("P25", "Median", "P75"):
                shown[col] = shown[col].map(
                    lambda v: k_text(v, sign)
                )  # "—", never "None"; rounds like the chart
            with st.expander(
                "Show data: salary percentiles",
                icon=":material/table:",
            ):
                st.dataframe(
                    shown[
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
                        "n_with_salary": st.column_config.NumberColumn(
                            "With a salary", format="%d"
                        ),
                        "coverage_pct": st.column_config.NumberColumn(
                            "Coverage",
                            format="%.0f%%",
                            help="Share of postings that state a salary.",
                        ),
                        "n_annualized": st.column_config.NumberColumn(
                            "Yearly figure known", format="%d"
                        ),
                    },
                    alt="Advertised yearly salary percentiles by role family and seniority",
                )
            st.caption(
                "Percentiles appear only where at least five yearly salaries exist; smaller "
                "groups are listed with a dash."
            )

    st.subheader("Internship pay (as posted)")
    intern = load(
        "SELECT salary_currency AS currency, salary_period AS period, count(*) AS n, "
        "min(coalesce(salary_min, salary_max)) AS low, "
        "median((coalesce(salary_min, salary_max) + coalesce(salary_max, salary_min)) / 2) "
        "AS mid, max(coalesce(salary_max, salary_min)) AS high "
        "FROM gold.fct_postings WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
        "AND seniority = 'intern' AND has_advertised_salary AND salary_period IS NOT NULL "
        "AND salary_currency IS NOT NULL "
        # a yearly range wider than this is a pay table for full-time levels, not intern pay
        "AND NOT (salary_period = 'year' AND salary_max > ? * salary_min) "
        "GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2",
        [app_config().pay.intern_max_range_ratio],
    )
    if intern.empty:
        st.caption("No internship postings with an advertised salary and a known pay period.")
    else:
        intern["Pay period"] = intern["period"].map(lambda v: f"per {v}")
        for col, name in (("low", "Lowest"), ("mid", "Median midpoint"), ("high", "Highest")):
            intern[name] = intern[col].map(lambda v: dash(v, "{:,.0f}"))
        with st.expander("Show data: internship pay", icon=":material/table:"):
            st.dataframe(
                intern[["currency", "Pay period", "n", "Lowest", "Median midpoint", "Highest"]],
                hide_index=True,
                column_config={
                    "currency": st.column_config.TextColumn("Currency", width="small"),
                    "n": st.column_config.NumberColumn("Postings", format="%d"),
                },
                alt="Internship pay in the unit it was posted",
            )
        st.caption(
            "Shown in the unit and currency of the posting, never converted to a yearly figure: "
            "an hourly or monthly internship rate is not a yearly salary. A yearly range that "
            "spans several job levels (a full-time pay table on an internship page) is left out."
        )

with lca_tab:
    visa_disclaimer()
    n_companies = int(load("SELECT count(*) AS n FROM gold.dim_company")["n"].iloc[0])
    st.caption(
        "Wages stated by employers on certified LCAs (lower bound of the filed range), for the "
        f"{n_companies} companies we track only. Not a market survey and not comparable "
        "one-to-one with advertised salary. Wages that are implausible after converting to a "
        "yearly figure (filer unit errors) are excluded, not corrected."
    )
    wages = load(
        "SELECT role_family, worksite_state, worksite_city, n_certified, n_with_wage, "
        "n_wage_excluded, coverage_pct, p25_annual, p50_annual, p75_annual "
        "FROM gold.agg_lca_wage_by_soc_city ORDER BY n_certified DESC"
    )
    if wages.empty:
        st.info("No LCA data loaded yet.")
    else:
        # Data and AI role families are the point of this site; "Other roles" (8,000 filings for
        # titles that match no data role) would drown them, so it is opt-in.
        families = [r for r in DATA_ROLES if r in set(wages["role_family"])]
        if "other" in set(wages["role_family"]):
            families.append("other")
        picked = st.multiselect(
            "Role family",
            families,
            default=[r for r in families if r != "other"],
            format_func=role_label,
            key="lca_roles",
            help="Data and AI roles by default. Add 'Other roles' to see every certified filing.",
        )
        wages = wages[wages["role_family"].isin(picked)]
        if wages.empty:
            st.info("Pick at least one role family.")
            st.stop()
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

        st.subheader("Median offered wage by role and worksite")
        by_site = (
            wages.dropna(subset=["Median"])
            .sort_values(["n_with_wage", "Median"], ascending=False)
            .head(15)
        )
        if by_site.empty:
            st.info(
                "No role and worksite has the five wages a median needs yet.",
                icon=":material/info:",
            )
        else:
            by_site = by_site.assign(
                Worksite=by_site["Role family"] + ", " + by_site["City"] + ", " + by_site["State"]
            )
            top = by_site.loc[by_site["Median"].idxmax()]
            key_insight(
                f"{top['Worksite']} has the highest median offered wage among the "
                f"{len(by_site)} busiest worksites shown: {k_text(top['Median'])} "
                f"(from {int(top['n_with_wage'])} filings)."
            )
            bar(
                by_site[["Worksite", "Median"]].rename(
                    columns={"Median": "Median offered wage ($K)"}
                ),
                x="Worksite",
                y="Median offered wage ($K)",
                horizontal=True,
                sort="-Median offered wage ($K)",
            )
            st.caption(
                "The 15 role and worksite pairs with the most usable wages that have at least five "
                "(lower bound of the filed range, per year). Employers' stated offers on public "
                "filings, not what a posting advertises, and not a market survey."
            )
        show_all = st.toggle(
            "Include groups with fewer than five wages (no percentiles)", key="lca_thin"
        )
        wages = wages if show_all else wages[wages["Median"].notna()]
        for col in ("P25", "Median", "P75"):
            wages[col] = wages[col].map(k_text)
        with st.expander(
            "Show data: LCA wages",
            icon=":material/table:",
        ):
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
                },
                alt="Offered wages on certified LCAs by role family and worksite",
            )
