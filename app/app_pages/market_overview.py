import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    REMOTE_LABELS,
    app_config,
    bar,
    city_map,
    human,
    key_insight,
    kpi_comparison,
    kpi_label,
    load,
    pct,
    role_label,
)
from jobpulse.config import load_city_coords

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

st.markdown(
    f"**Open roles across {int(counts['companies'])} tech employers' public job boards**, with "
    "hiring trends, advertised pay and public H-1B filing evidence for data and AI jobs."
)
days = app_config().kpi.delta_days
now_kpi, before_kpi = kpi_comparison(days)


def _delta(name: str) -> str | None:
    return insights.delta_text(
        now_kpi[name], None if before_kpi is None else before_kpi[name], unit=kpi_label(days)
    )


with st.container(horizontal=True):
    st.metric(
        "Open postings",
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
    + (
        " Changes compare the last two fully settled days (a posting counts as closed only after "
        "it is missing twice in a row)."
        if before_kpi
        else f" Changes {kpi_label(days)} appear once the data goes back that far."
    )
)

left, right = st.columns(2)
with left:
    st.subheader("Open postings by company")
    by_company = load(
        "SELECT name AS company, n_active AS postings FROM gold.dim_company WHERE n_active > 0 "
        "ORDER BY n_active DESC LIMIT 15"
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
        "WHERE is_active AND has_extraction AND NOT is_probable_duplicate "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    by_role["role"] = by_role["role_family"].map(role_label)
    key_insight(insights.role_insight(list(zip(by_role["role"], by_role["postings"], strict=True))))
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
    key_insight(
        insights.remote_insight(list(zip(remote["policy"], remote["postings"], strict=True)))
    )
    bar(remote, x="policy", y="postings", horizontal=True, sort="-postings")
with right:
    st.subheader("Top cities")
    cities = load(
        "SELECT city, count(*) AS postings FROM gold.fct_postings "
        "WHERE is_active AND NOT is_probable_duplicate AND city IS NOT NULL "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
    )
    quality = load(
        "SELECT numerator, denominator FROM gold.agg_data_quality WHERE metric = 'city_parsed'"
    )
    with_city = int(quality["numerator"].iloc[0]) if len(quality) else 0
    key_insight(
        insights.city_insight(list(zip(cities["city"], cities["postings"], strict=True)), with_city)
    )
    bar(cities, x="city", y="postings", horizontal=True, sort="-postings")
    if len(quality):
        row = quality.iloc[0]
        st.caption(
            f"A single city could be read for {pct(row['numerator'], row['denominator'])} of "
            "open postings; the rest are remote-only or list several locations."
        )

st.subheader("Where the open postings are")
by_city = load(
    "SELECT city, count(*) AS postings FROM gold.fct_postings "
    "WHERE is_active AND NOT is_probable_duplicate AND city IS NOT NULL GROUP BY 1"
)
city_rows = [(str(c), int(n)) for c, n in zip(by_city["city"], by_city["postings"], strict=True)]
mapped = [r for r in city_rows if r[0] in load_city_coords(app_config())]
key_insight(insights.concentration_insight([(c, float(n)) for c, n in mapped]))
drawn, left_off = city_map(city_rows)
st.caption(
    f"{drawn:,} open postings that name a single city are on the map; {left_off:,} more are in "
    "cities that have no map position yet. Dot area shows the number of postings. Positions are "
    "approximate city centres, not exact office locations. Remote-only and multi-location "
    "postings have no single city and are not drawn."
)
