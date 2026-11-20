-- One row per pipeline task run (ops.pipeline_runs, loaded by `jobpulse load-ops`). Counts and
-- cost only; no row content.
select
    attempt_id,
    task,
    source_id,
    snapshot_date,
    started_at,
    finished_at,
    duration_s,
    status,
    companies_total,
    companies_complete,
    companies_failed,
    rows_in,
    rows_kept,
    llm_cost_usd,
    env
from {{ source('ops', 'pipeline_runs') }}
