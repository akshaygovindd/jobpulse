-- Newest authoritative snapshot per board (source, board_id). The app computes age as
-- (now - newest_completed_at) and flags stale when it exceeds max_age_hours, so the banner stays
-- correct between builds; built_at says when this table was built.
select
    source_id,
    board_id,
    company,
    company_id,
    max(snapshot_date) as newest_snapshot_date,
    max(completed_at) as newest_completed_at,
    arg_max(record_count, completed_at) as newest_record_count,
    {{ var('freshness_max_age_hours') }} as max_age_hours,
    current_timestamp as built_at
from {{ ref('stg_snapshot_pointers') }}
group by source_id, board_id, company, company_id
