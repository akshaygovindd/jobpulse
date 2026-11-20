from datetime import UTC, datetime

import streamlit as st

from jobpulse.app_ui import (
    SOURCE_LABELS,
    STATUS_LABELS,
    TASK_LABELS,
    dash,
    date_chart,
    human,
    load,
    sentence,
)

fresh = load(
    "SELECT source_id, count(*) AS boards, max(newest_completed_at) AS newest, "
    "min(newest_completed_at) AS oldest_board, max(max_age_hours) AS max_age_hours, "
    "max(built_at) AS built_at FROM gold.fct_snapshot_freshness GROUP BY 1 ORDER BY 1"
)
now = datetime.now(UTC)
if fresh.empty:
    st.warning("No snapshots loaded yet.")
else:
    for col in ("newest", "oldest_board", "built_at"):
        fresh[col] = fresh[col].dt.tz_convert("UTC")  # shown and labelled as UTC
    fresh["age_h"] = fresh["newest"].map(lambda t: (now - t.to_pydatetime()).total_seconds() / 3600)
    fresh["oldest_age_h"] = fresh["oldest_board"].map(
        lambda t: (now - t.to_pydatetime()).total_seconds() / 3600
    )
    fresh["Source"] = fresh["source_id"].map(lambda v: human(v, SOURCE_LABELS))
    limit = float(fresh["max_age_hours"].iloc[0])
    stale = fresh[(fresh["age_h"] > limit) | (fresh["oldest_age_h"] > limit)]
    if len(stale):
        st.error(
            f"Stale data: {', '.join(stale['Source'])} has a snapshot older than "
            f"{limit:g} hours. The numbers on other pages may be behind.",
            title="Freshness warning",
            icon=":material/schedule:",
        )
    else:
        st.success(
            f"All sources have a snapshot newer than {limit:g} hours.",
            icon=":material/check_circle:",
        )
    with st.expander("Show data: snapshot freshness per source", icon=":material/table:"):
        st.dataframe(
            fresh[["Source", "boards", "newest", "age_h", "oldest_age_h"]],
            hide_index=True,
            column_config={
                "Source": st.column_config.TextColumn(width="medium"),
                "boards": st.column_config.NumberColumn("Companies", format="%d"),
                "newest": st.column_config.DatetimeColumn(
                    "Newest snapshot (UTC)", format="YYYY-MM-DD HH:mm"
                ),
                "age_h": st.column_config.NumberColumn("Newest age (hours)", format="%.0f"),
                "oldest_age_h": st.column_config.NumberColumn(
                    "Oldest company age (hours)", format="%.0f"
                ),
            },
            alt="Snapshot freshness per source",
        )
    st.caption(f"Analytics tables last built {fresh['built_at'].max():%d %b %Y, %H:%M} UTC.")

st.subheader("Data quality")
quality = load("SELECT metric, description, numerator, denominator FROM gold.agg_data_quality")
if not quality.empty:
    quality["share"] = quality.apply(
        lambda r: (
            dash(100 * r["numerator"] / r["denominator"], "{:.0f}%") if r["denominator"] else "—"
        ),
        axis=1,
    )
    quality["numerator"] = quality["numerator"].map(dash)
    quality["denominator"] = quality["denominator"].map(dash)
    quality["description"] = quality["description"].map(sentence)
    st.caption(
        "A low share is expected where only some postings are read by the AI: a title filter "
        "chooses the data and AI roles, and the rest are counted but not analyzed."
    )
    with st.expander("Show data: coverage of key fields", icon=":material/table:"):
        st.dataframe(
            quality[["description", "numerator", "denominator", "share"]],
            hide_index=True,
            column_config={
                "description": st.column_config.TextColumn("Field", width="large"),
                "numerator": st.column_config.TextColumn("Have it", width="small"),
                "denominator": st.column_config.TextColumn("Out of", width="small"),
                "share": st.column_config.TextColumn("Share", width="small"),
            },
            alt="Coverage of key fields",
        )

st.subheader("Recent runs")
runs = load(
    "SELECT snapshot_date, started_at, task, source_id, status, duration_s, "
    "companies_complete, companies_failed, rows_kept FROM gold.fct_pipeline_runs "
    "ORDER BY started_at DESC NULLS LAST LIMIT 40"
)
if runs.empty:
    st.info("No run records are available yet.")
else:
    failed = runs[runs["status"] != "success"]
    if len(failed):
        # Say which step failed and when, and whether a later run of the same step succeeded.
        notes = []
        for _, f in failed.iterrows():
            later_ok = runs[
                (runs["task"] == f["task"])
                & (runs["status"] == "success")
                & (runs["started_at"] > f["started_at"])
            ]
            when = (
                f"{f['started_at']:%d %b %H:%M} UTC" if f["started_at"] == f["started_at"] else "?"
            )
            notes.append(
                f"{human(f['task'], TASK_LABELS)} (data date {f['snapshot_date']}, started {when})"
                + (", later succeeded" if len(later_ok) else ", not yet re-run successfully")
            )
        st.warning(
            f"{len(failed)} of the last {len(runs)} runs did not succeed: "
            + "; ".join(notes)
            + ".",
            icon=":material/warning:",
        )
        runs = runs.sort_values(
            "status", key=lambda s: s == "success", kind="stable"
        )  # failures first
    runs["Step"] = runs["task"].map(lambda v: human(v, TASK_LABELS))
    runs["Source"] = runs["source_id"].map(lambda v: human(v, SOURCE_LABELS))
    runs["Result"] = runs["status"].map(lambda v: human(v, STATUS_LABELS))
    for col in ("duration_s", "companies_complete", "companies_failed", "rows_kept"):
        runs[col] = runs[col].map(dash)  # a missing figure shows as an em dash, never "None"
    with st.expander("Show data: most recent pipeline runs", icon=":material/table:"):
        st.dataframe(
            runs[
                [
                    "snapshot_date",
                    "Step",
                    "Source",
                    "Result",
                    "duration_s",
                    "companies_complete",
                    "companies_failed",
                    "rows_kept",
                ]
            ],
            hide_index=True,
            column_config={
                "Step": st.column_config.TextColumn(width="medium"),
                "Source": st.column_config.TextColumn(width="small"),
                "Result": st.column_config.TextColumn(width="small"),
                "snapshot_date": st.column_config.DateColumn("Data date"),
                "duration_s": st.column_config.TextColumn("Seconds", width="small"),
                "companies_complete": st.column_config.TextColumn(
                    "Companies complete", width="small"
                ),
                "companies_failed": st.column_config.TextColumn("Companies failed", width="small"),
                "rows_kept": st.column_config.TextColumn("Rows kept", width="small"),
            },
            alt="Most recent pipeline runs",
        )

st.subheader("Ingest outcomes by day")
ingest = load(
    "SELECT strftime(snapshot_date, '%Y-%m-%d') AS day, sum(n_authoritative) AS boards "
    "FROM gold.agg_ingest_status_daily GROUP BY 1 ORDER BY 1"
)
if not ingest.empty:
    # Re-runs of the same day fetch each board again; only the snapshot that was kept counts.
    date_chart(ingest, x="day", y="boards", y_title="Companies with a complete snapshot")
    st.caption(
        "Each company is counted once per day, for the snapshot that the analytics use. "
        "Re-runs and failed attempts on the same day are not added up."
    )

st.subheader("AI extraction cost")
cost = load(
    "SELECT strftime(call_date, '%Y-%m-%d') AS day, model, mode, sum(cost_usd) AS cost_usd, "
    "sum(n_calls) AS calls FROM gold.agg_llm_cost_daily GROUP BY 1, 2, 3 ORDER BY 1"
)
if cost.empty:
    st.info("No extraction calls recorded yet.")
else:
    # Evaluation calls (a one-off model comparison) are not part of what the pipeline costs to run.
    prod = cost[cost["mode"] != "eval"]
    evaluation = cost[cost["mode"] == "eval"]
    with st.container(horizontal=True):
        st.metric("Pipeline spend", f"${prod['cost_usd'].sum():,.2f}", border=True)
        st.metric("AI calls", f"{int(prod['calls'].sum()):,}", border=True)
        if not evaluation.empty:
            st.metric(
                "Model evaluation (one-off)",
                f"${evaluation['cost_usd'].sum():,.2f}",
                help=f"{int(evaluation['calls'].sum())} calls to compare two models; not part of "
                "the daily pipeline.",
                border=True,
            )
    if not prod.empty:
        date_chart(prod, x="day", y="cost_usd", series="model", y_title="Spend (US dollars)")
    st.caption(
        "Pipeline spend is the daily extraction of new and changed postings; evaluation calls are "
        "shown separately. Cost is computed from token counts and the prices in config at call "
        "time, shown to the cent because the amounts are small."
    )
