from datetime import UTC, datetime

import streamlit as st

from jobpulse.app_ui import bar, load

fresh = load(
    "SELECT source_id, count(*) AS boards, max(newest_completed_at) AS newest, "
    "min(newest_completed_at) AS oldest_board, max(max_age_hours) AS max_age_hours, "
    "max(built_at) AS built_at FROM gold.fct_snapshot_freshness GROUP BY 1 ORDER BY 1"
)
now = datetime.now(UTC)
if fresh.empty:
    st.warning("No snapshots loaded yet.")
else:
    fresh["age_h"] = fresh["newest"].map(lambda t: (now - t.to_pydatetime()).total_seconds() / 3600)
    fresh["oldest_age_h"] = fresh["oldest_board"].map(
        lambda t: (now - t.to_pydatetime()).total_seconds() / 3600
    )
    limit = float(fresh["max_age_hours"].iloc[0])
    stale = fresh[(fresh["age_h"] > limit) | (fresh["oldest_age_h"] > limit)]
    if len(stale):
        st.error(
            f"Stale data: {', '.join(stale['source_id'])} has a snapshot older than "
            f"{limit:g} hours. "
            "The numbers on other pages may be behind.",
            title="Freshness warning",
            icon=":material/schedule:",
        )
    else:
        st.success(
            f"All sources have a snapshot newer than {limit:g} hours.",
            icon=":material/check_circle:",
        )
    st.dataframe(
        fresh[["source_id", "boards", "newest", "age_h", "oldest_age_h"]],
        hide_index=True,
        column_config={
            "source_id": "Source",
            "boards": "Companies",
            "newest": st.column_config.DatetimeColumn(
                "Newest snapshot (UTC)", format="YYYY-MM-DD HH:mm"
            ),
            "age_h": st.column_config.NumberColumn("Newest age (h)", format="%.1f"),
            "oldest_age_h": st.column_config.NumberColumn("Oldest company age (h)", format="%.1f"),
        },
        alt="Snapshot freshness per source",
    )
    st.caption(f"Gold last built {fresh['built_at'].max():%Y-%m-%d %H:%M} UTC.")

st.subheader("Data quality")
quality = load("SELECT metric, description, numerator, denominator FROM gold.agg_data_quality")
if not quality.empty:
    quality["share"] = quality.apply(
        lambda r: 100 * r["numerator"] / r["denominator"] if r["denominator"] else None, axis=1
    )
    st.dataframe(
        quality[["description", "numerator", "denominator", "share"]],
        hide_index=True,
        column_config={
            "description": "Field",
            "numerator": "Have it",
            "denominator": "Out of",
            "share": st.column_config.ProgressColumn(
                "Share %", min_value=0, max_value=100, format="%.0f"
            ),
        },
        alt="Coverage of key fields",
    )

st.subheader("Recent runs")
runs = load(
    "SELECT snapshot_date, task, source_id, status, duration_s, companies_complete, "
    "companies_failed, rows_kept, llm_cost_usd FROM gold.fct_pipeline_runs "
    "ORDER BY started_at DESC NULLS LAST LIMIT 40"
)
if runs.empty:
    st.info("No run records loaded yet (`jobpulse load-ops`).")
else:
    failed = runs[runs["status"] != "success"]
    if len(failed):
        st.warning(f"{len(failed)} of the last {len(runs)} runs did not succeed.")
    st.dataframe(runs, hide_index=True, alt="Most recent pipeline runs")

st.subheader("Ingest outcomes by day")
ingest = load(
    "SELECT snapshot_date, status, sum(n_boards) AS boards FROM gold.agg_ingest_status_daily "
    "GROUP BY 1, 2 ORDER BY 1"
)
if not ingest.empty:
    bar(ingest, x="snapshot_date", y="boards", color="status")

st.subheader("AI extraction cost")
cost = load(
    "SELECT call_date, model, sum(cost_usd) AS cost_usd, sum(n_calls) AS calls "
    "FROM gold.agg_llm_cost_daily GROUP BY 1, 2 ORDER BY 1"
)
if cost.empty:
    st.info("No extraction calls recorded yet.")
else:
    with st.container(horizontal=True):
        st.metric("Total spend (USD)", f"{cost['cost_usd'].sum():.2f}", border=True)
        st.metric("Calls", f"{int(cost['calls'].sum()):,}", border=True)
    bar(cost, x="call_date", y="cost_usd", color="model")
    st.caption("Cost is computed from token counts and the prices in config at call time.")
