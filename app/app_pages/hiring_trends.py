import streamlit as st

from jobpulse import insights
from jobpulse.app_ui import (
    ROLE_LABELS,
    app_config,
    bar,
    date_chart,
    key_insight,
    load,
    role_label,
    skill_label,
)

bounds = load(
    "SELECT min(first_seen_date) AS first_day, max(first_seen_date) AS last_day "
    "FROM gold.fct_postings"
).iloc[0]
if bounds["first_day"] is None or bounds["first_day"] != bounds["first_day"]:
    st.warning("No postings yet.")
    st.stop()
first_day = bounds["first_day"]

st.subheader("New and closed postings per day")
left, right = st.columns(2)
with left:
    st.markdown("**New postings**")
    new = load(
        "SELECT strftime(first_seen_date, '%Y-%m-%d') AS day, count(*) AS postings "
        "FROM gold.fct_postings WHERE NOT is_probable_duplicate "
        "AND first_seen_date > (SELECT min(first_seen_date) FROM gold.fct_postings) "
        "GROUP BY 1 ORDER BY 1"
    )
    key_insight(insights.daily_flow_insight(list(zip(new["day"], new["postings"], strict=True))))
    date_chart(new, x="day", y="postings", y_title="New postings")
with right:
    st.markdown("**Closed postings**")
    closed = load(
        "SELECT strftime(closed_date, '%Y-%m-%d') AS day, count(*) AS postings "
        "FROM gold.fct_postings WHERE NOT is_probable_duplicate AND closed_date IS NOT NULL "
        "GROUP BY 1 ORDER BY 1"
    )
    key_insight(
        insights.daily_flow_insight(
            list(zip(closed["day"], closed["postings"], strict=True)), noun="closed postings"
        )
    )
    date_chart(closed, x="day", y="postings", y_title="Closed postings")
st.caption(
    f"The first day of data ({first_day:%d %b %Y}) is left out of the chart: every open posting "
    "looked new that day, so it is a baseline, not a hiring spike. A posting counts as closed "
    "once it is missing from two daily snapshots in a row, so closures start two days after the "
    "baseline. Days when the daily fetch was incomplete show as dips."
)

st.subheader("Skills employers ask for")
roles = load("SELECT DISTINCT role_family FROM gold.agg_skill_demand_weekly ORDER BY 1")[
    "role_family"
].tolist()
if not roles:
    st.info("No analyzed postings yet, so there is no skill data.")
    st.stop()
role = st.segmented_control(
    "Role family",
    roles,
    default=roles[0],
    format_func=role_label,
    key="trend_role",
)
if not role:
    st.stop()
skills = load(
    "SELECT d.canonical_name AS skill, strftime(a.week_start, '%Y-%m-%d') AS week, "
    "a.share_of_postings AS share FROM gold.agg_skill_demand_weekly a "
    "JOIN gold.dim_skill d USING (skill_id) WHERE a.role_family = ?",
    [role],
)
if skills.empty:
    st.info("No skills for this role family yet.")
    st.stop()
skills["skill"] = skills["skill"].map(skill_label)  # display names; data keeps canonical ones
latest = skills["week"].max()
top = skills[skills["week"] == latest].sort_values("share", ascending=False).head(15).copy()
top["Share of postings (%)"] = 100 * top["share"]
st.caption(
    f"Share of analyzed {ROLE_LABELS.get(role, role)} postings open in the week starting "
    f"{latest} that mention the skill."
)
key_insight(
    insights.skill_insight(
        list(zip(top["skill"], top["Share of postings (%)"], strict=True)),
        ROLE_LABELS.get(role, role),
    )
)
bar(top, x="skill", y="Share of postings (%)", horizontal=True, sort="-Share of postings (%)")

last_day = load("SELECT max(newest_snapshot_date) AS d FROM gold.fct_snapshot_freshness")["d"].iloc[
    0
]
needed = app_config().trends.min_full_weeks
weeks = insights.full_weeks(first_day.date(), last_day.date()) if last_day == last_day else 0
if weeks < needed:
    st.caption(
        f"Skills over time appears once there are at least {needed} full weeks of history "
        f"(Monday to Sunday); there {'is' if weeks == 1 else 'are'} {weeks} so far. Fewer weeks "
        "would show noise, not trends."
    )
    st.stop()
chosen = st.multiselect(
    "Skills over time (weeks start on Mondays)",
    sorted(skills["skill"].unique()),
    default=top["skill"].head(3).tolist(),
)
if chosen:
    picked = skills[skills["skill"].isin(chosen)].copy()
    picked["Share of postings (%)"] = 100 * picked["share"]
    date_chart(
        picked,
        x="week",
        y="Share of postings (%)",
        kind="line",
        series="skill",
    )
