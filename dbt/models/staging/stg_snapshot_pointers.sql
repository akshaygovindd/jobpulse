select
    source_id,
    board_id,
    company,
    -- company_id: lowercase, non-alphanumerics collapsed to '-'
    trim(regexp_replace(lower(company), '[^a-z0-9]+', '-', 'g'), '-') as company_id,
    snapshot_date,
    attempt_id,
    record_count,
    completed_at
from {{ source('bronze', 'snapshot_pointers') }}
