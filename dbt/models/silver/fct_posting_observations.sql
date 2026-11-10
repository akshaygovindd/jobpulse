-- One row per (posting, authoritative snapshot date after the posting was first seen).
-- observed = true  : the posting is in that authoritative snapshot.
-- observed = false : the company has an authoritative snapshot that date and the posting is absent.
-- Dates with no pointer produce no row at all: a missing snapshot is never an absence.
with seen as (
    select posting_key, source_id, board_id, snapshot_date, attempt_id, content_hash
    from {{ ref('stg_raw_postings') }}
),

first_seen as (
    select posting_key, source_id, board_id, min(snapshot_date) as first_seen_date
    from seen
    group by posting_key, source_id, board_id
),

absent as (
    select
        f.posting_key,
        f.source_id,
        f.board_id,
        p.snapshot_date,
        p.attempt_id,
        cast(null as varchar) as content_hash,
        false as observed
    from first_seen as f
    inner join {{ ref('stg_snapshot_pointers') }} as p
        on f.source_id = p.source_id
        and f.board_id = p.board_id
        and p.snapshot_date > f.first_seen_date
    where not exists (
        select 1 from seen as s
        where s.posting_key = f.posting_key and s.snapshot_date = p.snapshot_date
    )
)

select posting_key, source_id, board_id, snapshot_date, attempt_id, content_hash, true as observed
from seen
union all
select posting_key, source_id, board_id, snapshot_date, attempt_id, content_hash, observed
from absent
