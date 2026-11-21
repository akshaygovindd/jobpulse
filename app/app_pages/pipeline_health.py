from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from jobpulse.app_ui import (
    SOURCE_LABELS,
    STATUS_LABELS,
    TASK_LABELS,
    dash,
    date_chart,
    human,
    load,
    pct,
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
            pct(r["numerator"], r["denominator"]) if r["denominator"] else "—"
        ),  # 99.7%, not 100%
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
    if runs["started_at"].dt.tz is not None:  # labelled UTC below, so never the server's zone
        runs["started_at"] = runs["started_at"].dt.tz_convert("UTC")
    failed = runs[runs["status"] != "success"]
    if len(failed):
        # Plain words: which step failed, when, and whether a later run of the same step succeeded.
        notes = []
        recovered = True
        for _, f in failed.iterrows():
            later_ok = runs[
                (runs["task"] == f["task"])
                & (runs["status"] == "success")
                & (runs["started_at"] > f["started_at"])
            ]
            recovered = recovered and bool(len(later_ok))
            when = (
                f"{f['started_at']:%d %b at %H:%M} UTC"
                if f["started_at"] == f["started_at"]
                else "?"
            )
            day = f"{f['snapshot_date']:%d %b}" if f["snapshot_date"] == f["snapshot_date"] else "?"
            notes.append(
                f'the "{human(f["task"], TASK_LABELS)}" step for the {day} data failed on {when}'
                + (
                    ", and a later run of the same step succeeded"
                    if len(later_ok)
                    else ", and it has not been re-run successfully yet"
                )
            )
        count = "One run" if len(failed) == 1 else f"{len(failed)} runs"
        message = (
            f"{count} of the last {len(runs)} did not succeed: "
            + "; ".join(notes)
            + ". The daily pipeline can be re-run safely, so a failed step is simply tried again."
        )
        if recovered:
            st.info(message, icon=":material/info:")
        else:
            st.warning(message, icon=":material/warning:")
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
                    "Companies complete", width="medium"
                ),
                "companies_failed": st.column_config.TextColumn("Companies failed", width="medium"),
                "rows_kept": st.column_config.TextColumn("Rows kept", width="medium"),
            },
            alt="Most recent pipeline runs",
        )

st.subheader("Company fetches by day")
ingest = load(
    "SELECT strftime(snapshot_date, '%Y-%m-%d') AS day, "
    "sum(n_companies) FILTER (WHERE status <> 'complete') AS failed, "
    "max(n_total) AS companies FROM (SELECT snapshot_date, status, n_companies, "
    "sum(n_companies) OVER (PARTITION BY snapshot_date) AS n_total "
    "FROM gold.agg_ingest_status_daily) GROUP BY 1 ORDER BY 1"
)
if not ingest.empty:
    ingest["failed"] = ingest["failed"].fillna(0)
    if ingest["failed"].sum() == 0:
        st.success(
            f"Every company was fetched completely on each of the {len(ingest)} days of data "
            f"(up to {int(ingest['companies'].max())} companies a day).",
            icon=":material/check_circle:",
        )
    else:
        date_chart(
            ingest,
            x="day",
            y="failed",
            y_title="Companies with a failed or incomplete fetch",
        )
    st.caption(
        "Counts companies (not attempts) whose fetch failed or came back incomplete on a day, "
        "in any attempt. A company that failed and then succeeded on a re-run is still counted "
        "here; the analytics use only the complete snapshot, and a failed or incomplete fetch "
        "never closes a posting."
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
        # Days with no spend are drawn as $0 (a missing bar would look like missing data).
        every_day = sorted(set(prod["day"]) | set(ingest["day"] if not ingest.empty else []))
        have_days = set(prod["day"])
        zeros = [
            {"day": d, "model": prod["model"].iloc[0], "mode": "sync", "cost_usd": 0.0, "calls": 0}
            for d in every_day
            if d not in have_days
        ]
        if zeros:
            prod = pd.concat([prod, pd.DataFrame(zeros)], ignore_index=True)
        date_chart(
            prod,
            x="day",
            y="cost_usd",
            series="model",
            y_title="Spend (US dollars)",
            y_format="$.2f",
        )
    st.caption(
        "Pipeline spend is the daily extraction of new and changed postings; evaluation calls are "
        "shown separately. Cost is computed from token counts and the prices in config at call "
        "time, shown to the cent because the amounts are small."
    )
