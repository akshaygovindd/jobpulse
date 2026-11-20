import streamlit as st

from jobpulse.app_ui import bar, load

weekly = load(
    "SELECT date_trunc('week', first_seen_date) AS week, count(*) AS new_postings "
    "FROM gold.fct_postings WHERE NOT is_probable_duplicate GROUP BY 1 ORDER BY 1"
)
if weekly.empty:
    st.warning("No postings yet.")
    st.stop()

st.subheader("New postings per week")
bar(weekly, x="week", y="new_postings")
st.caption(
    "A posting counts in the week we first saw it. The first week of data (when the pipeline "
    f"started, {weekly['week'].iloc[0]:%d %b %Y}) is a baseline: every posting looks new then, so "
    "do not read it as a hiring spike."
)

st.subheader("Skills employers ask for")
roles = load("SELECT DISTINCT role_family FROM gold.agg_skill_demand_weekly ORDER BY 1")[
    "role_family"
].tolist()
if not roles:
    st.info("No extracted postings yet, so there is no skill data.")
    st.stop()
role = st.segmented_control("Role family", roles, default=roles[0], key="trend_role")
if not role:
    st.stop()
skills = load(
    "SELECT d.canonical_name AS skill, a.week_start, a.active_postings, a.share_of_postings "
    "FROM gold.agg_skill_demand_weekly a JOIN gold.dim_skill d USING (skill_id) "
    "WHERE a.role_family = ?",
    [role],
)
if skills.empty:
    st.info("No skills for this role family yet.")
    st.stop()
latest = skills["week_start"].max()
top = (
    skills[skills["week_start"] == latest]
    .sort_values("share_of_postings", ascending=False)
    .head(15)
)
st.caption(
    f"Share of extracted {role.replace('_', ' ')} postings active in the week of "
    f"{latest:%d %b %Y} that mention the skill."
)
bar(top, x="skill", y="share_of_postings", horizontal=True, sort="-share_of_postings")

chosen = st.multiselect(
    "Skills over time", sorted(skills["skill"].unique()), default=top["skill"].head(3).tolist()
)
if chosen:
    over_time = skills[skills["skill"].isin(chosen)].pivot_table(
        index="week_start", columns="skill", values="share_of_postings"
    )
    st.line_chart(over_time)
    if len(over_time) < 3:
        st.caption("Fewer than three weeks of history: trends are not meaningful yet.")
