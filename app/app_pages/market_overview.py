import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    REMOTE_LABELS,
    SENIORITY_LABELS,
    app_config,
    bar,
    city_coords,
    city_map,
    human,
    key_insight,
    kpi_comparison,
    kpi_label,
    load,
    pct,
    role_label,
)

counts = load(
    "SELECT count(*) AS n, count(*) FILTER (WHERE is_active) AS active, "
    "count(*) FILTER (WHERE is_active AND has_extraction) AS analyzed, "
    "count(DISTINCT company_id) FILTER (WHERE is_active) AS companies, "
    "count(DISTINCT city) FILTER (WHERE is_active) AS cities "
    "FROM gold.fct_postings WHERE NOT is_probable_duplicate"
).iloc[0]

if not counts["n"]:
    st.warning("No postings yet. The data is refreshed daily; please check back soon.")
    st.stop()

target = load(
    "SELECT numerator, denominator FROM gold.agg_data_quality "
    "WHERE metric = 'target_roles_analyzed'"
)
t_done = int(target["numerator"].iloc[0]) if len(target) else int(counts["analyzed"])
t_all = int(target["denominator"].iloc[0]) if len(target) else 0

st.markdown(
    f"**Open roles across {int(counts['companies'])} tech employers' public job boards**, with "
    "hiring trends, advertised pay and public H-1B filing evidence for data and AI jobs."
)
days = app_config().kpi.delta_days
now_kpi, before_kpi = kpi_comparison(days)

# ---- filters: they apply to every number, chart and the map on this page --------------------
# Role family, seniority and remote policy come from the AI's reading of a posting, so a filtered
# view covers analyzed postings only.
options = load(
    "SELECT DISTINCT role_family, seniority, remote_policy FROM gold.fct_postings "
    "WHERE is_active AND has_extraction AND NOT is_probable_duplicate"
)
with st.container(horizontal=True):
    pick_roles = st.multiselect(
        "Role family",
        sorted(options["role_family"].dropna().unique()),
        format_func=role_label,
        key="mkt_roles",
    )
    pick_levels = st.multiselect(
        "Seniority",
        [v for v in SENIORITY_LABELS if v in set(options["seniority"].dropna())],
        format_func=lambda v: human(v, SENIORITY_LABELS),
        key="mkt_levels",
    )
    pick_remote = st.multiselect(
        "Remote policy",
        sorted(options["remote_policy"].dropna().unique()),
        format_func=lambda v: human(v, REMOTE_LABELS),
        key="mkt_remote",
    )
filtered = bool(pick_roles or pick_levels or pick_remote)
conditions = ["is_active", "NOT is_probable_duplicate"]
params: list[str] = []
for column, picked in (
    ("role_family", pick_roles),
    ("seniority", pick_levels),
    ("remote_policy", pick_remote),
):
    if picked:
        conditions.append(f"{column} IN ({', '.join('?' for _ in picked)})")
        params += picked
base_where = " AND ".join(conditions)
analyzed_where = base_where + " AND has_extraction"
city_where = analyzed_where if filtered else base_where
if filtered:
    counts = load(
        "SELECT count(*) AS n, count(*) AS active, count(*) AS analyzed, "
        "count(DISTINCT company_id) AS companies, count(DISTINCT city) AS cities, "
        "count(city) AS with_city "
        f"FROM gold.fct_postings WHERE {analyzed_where}",
        params,
    ).iloc[0]
    now_kpi, before_kpi = {"postings": 0, "companies": 0, "cities": 0}, None
    st.info(
        f"Filtered view: {int(counts['active']):,} analyzed open postings match. Filters use the "
        "AI's reading of each posting, so postings that were not analyzed are not included.",
        icon=":material/filter_alt:",
    )
    if not counts["active"]:
        st.warning("No postings match these filters.")
        st.stop()


def _delta(name: str) -> str | None:
    return insights.delta_text(
        now_kpi[name], None if before_kpi is None else before_kpi[name], unit=kpi_label(days)
    )


with st.container(horizontal=True):
    st.metric(
        "Open postings (matching)" if filtered else "Open postings",
        f"{int(counts['active']):,}",
        delta=_delta("postings"),
        delta_color="off",
        border=True,
    )
    st.metric(
        "Companies hiring",
        f"{int(counts['companies']):,}",
        delta=_delta("companies"),
        delta_color="off",
        border=True,
    )
    st.metric(
        "Cities",
        f"{int(counts['cities']):,}",
        delta=_delta("cities"),
        delta_color="off",
        border=True,
    )
    if not filtered:  # with a filter every number here already counts analyzed postings only
        st.metric(
            "Data & AI roles analyzed",
            f"{t_done:,}",
            help="The AI reads only postings whose titles look like data and AI roles (a title "
            "filter), to keep cost low. Role family, seniority, salary, skills and sponsorship "
            "wording come from those postings.",
            border=True,
        )

if filtered:
    # Every share on this page uses the filtered set, so nothing here may mention the unfiltered
    # totals (a filtered count minus the unfiltered one is meaningless, and negative).
    st.caption(
        f"Every number and chart below covers the {int(counts['active']):,} analyzed open postings "
        "that match the filters. Duplicates excluded."
    )
else:
    st.caption(
        f"The AI reads only data and AI roles (chosen by job title). Of {t_all:,} open postings "
        f"in those roles, {t_done:,} ({pct(t_done, t_all)}) have been analyzed so far. The other "
        f"{int(counts['active']) - t_all:,} open postings are counted here but not read by the "
        "AI, so the role, remote and pay breakdowns below cover analyzed postings only. "
        "Duplicates excluded."
        + (
            " Changes compare the last two fully settled days (a posting counts as closed only "
            "after it is missing twice in a row)."
            if before_kpi
            else f" Changes {kpi_label(days)} appear once the data goes back that far."
        )
    )

left, right = st.columns(2)
with left:
    st.subheader("Open postings by company")
    by_company = load(
        "SELECT c.name AS company, count(*) AS postings FROM gold.fct_postings p "
        "JOIN gold.dim_company c USING (company_id) "
        f"WHERE {city_where} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 15",
        params,
    )
    key_insight(
        insights.company_insight(
            list(zip(by_company["company"], by_company["postings"], strict=True)),
            int(counts["active"]),
        )
    )
    bar(by_company, x="company", y="postings", horizontal=True, sort="-postings")
with right:
    st.subheader("Role family (analyzed postings)")
    by_role = load(
        "SELECT role_family, count(*) AS postings FROM gold.fct_postings "
        f"WHERE {analyzed_where} GROUP BY 1 ORDER BY 2 DESC",
        params,
    )
    by_role["role"] = by_role["role_family"].map(role_label)
    key_insight(insights.role_insight(list(zip(by_role["role"], by_role["postings"], strict=True))))
    bar(by_role, x="role", y="postings", horizontal=True, sort="-postings")

left, right = st.columns(2)
with left:
    st.subheader("Remote policy (analyzed postings)")
    remote = load(
        "SELECT remote_policy, count(*) AS postings FROM gold.fct_postings "
        f"WHERE {analyzed_where} GROUP BY 1 ORDER BY 2 DESC",
        params,
    )
    remote["policy"] = remote["remote_policy"].map(lambda v: human(v, REMOTE_LABELS))
    key_insight(
        insights.remote_insight(list(zip(remote["policy"], remote["postings"], strict=True)))
    )
    bar(remote, x="policy", y="postings", horizontal=True, sort="-postings")
with right:
    st.subheader("Top cities")
    cities = load(
        "SELECT city, count(*) AS postings FROM gold.fct_postings "
        f"WHERE {city_where} AND city IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 15",
        params,
    )
    if filtered:
        with_city, of_postings = int(counts["with_city"]), int(counts["active"])
    else:
        quality = load(
            "SELECT numerator, denominator FROM gold.agg_data_quality WHERE metric = 'city_parsed'"
        )
        with_city = int(quality["numerator"].iloc[0]) if len(quality) else 0
        of_postings = int(quality["denominator"].iloc[0]) if len(quality) else 0
    key_insight(
        insights.city_insight(list(zip(cities["city"], cities["postings"], strict=True)), with_city)
    )
    bar(cities, x="city", y="postings", horizontal=True, sort="-postings")
    if of_postings:
        st.caption(
            f"A single city could be read for {pct(with_city, of_postings)} of "
            f"{'the matching' if filtered else 'open'} postings; the rest are remote-only or list "
            "several locations."
        )

st.subheader("Where the open postings are")
by_city = load(
    "SELECT city, count(*) AS postings FROM gold.fct_postings "
    f"WHERE {city_where} AND city IS NOT NULL GROUP BY 1",
    params,
)
city_rows = [(str(c), int(n)) for c, n in zip(by_city["city"], by_city["postings"], strict=True)]
coords = city_coords()  # parsed once per process, not once per city
mapped = [r for r in city_rows if r[0] in coords]
key_insight(insights.concentration_insight([(c, float(n)) for c, n in mapped]))
drawn, left_off = city_map(city_rows)
if left_off == 1:
    off_text = "1 more is in a city that has no map position yet"
else:
    off_text = f"{left_off:,} more are in cities that have no map position yet"
st.caption(
    f"{drawn:,} open postings that name a single city are on the map; {off_text}. Dot area shows "
    "the number of postings. Positions are approximate city centres, not exact office "
    "locations. Remote-only and multi-location postings have no single city and are not drawn."
)
