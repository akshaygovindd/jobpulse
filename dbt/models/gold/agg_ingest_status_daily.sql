-- Per-board ingest outcomes by snapshot date (ops.company_snapshots): how many boards were complete
-- or failed and why. Grain: (snapshot_date, source_id, status, reason_code).
-- ops.company_snapshots has one row per ATTEMPT, and a day can have several attempts (re-runs), so
-- n_boards and n_authoritative count attempts. n_companies and n_authoritative_companies count
-- DISTINCT boards (one company each), which is what a "companies per day" chart must use.
select
    snapshot_date,
    source_id,
    status,
    coalesce(reason_code, 'none') as reason_code,
    count(*) as n_boards,
    count(*) filter (where is_authoritative) as n_authoritative,
    count(distinct board_id) as n_companies,
    count(distinct board_id) filter (where is_authoritative) as n_authoritative_companies
from {{ source('ops', 'company_snapshots') }}
group by snapshot_date, source_id, status, coalesce(reason_code, 'none')
