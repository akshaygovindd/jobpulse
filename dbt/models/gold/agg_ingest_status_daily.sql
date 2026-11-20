-- Per-board ingest outcomes by snapshot date (ops.company_snapshots): how many boards were complete
-- or failed and why. Grain: (snapshot_date, source_id, status, reason_code).
select
    snapshot_date,
    source_id,
    status,
    coalesce(reason_code, 'none') as reason_code,
    count(*) as n_boards,
    count(*) filter (where is_authoritative) as n_authoritative
from {{ source('ops', 'company_snapshots') }}
group by snapshot_date, source_id, status, coalesce(reason_code, 'none')
