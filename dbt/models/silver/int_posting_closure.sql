-- Closure rule (CLAUDE.md): a posting is closed only after N absences on calendar-adjacent
-- dates that both have an authoritative pointer. A date without a pointer has no row in
-- fct_posting_observations, so it breaks the run instead of counting as an absence.
-- Only absences after the posting's last sighting count; a reappearing posting is active again.
with last_seen as (
    select posting_key, max(snapshot_date) as last_seen_date
    from {{ ref('fct_posting_observations') }}
    where observed
    group by posting_key
),

tail_absent as (
    select
        o.posting_key,
        o.source_id,
        o.board_id,
        o.snapshot_date,
        -- consecutive calendar dates share the same grp
        o.snapshot_date - cast(
            row_number() over (partition by o.posting_key order by o.snapshot_date) as integer
        ) as grp
    from {{ ref('fct_posting_observations') }} as o
    inner join last_seen as l on o.posting_key = l.posting_key
    where not o.observed and o.snapshot_date > l.last_seen_date
),

islands as (
    select
        posting_key,
        source_id,
        board_id,
        min(snapshot_date) as island_start,
        count(*) as n_absent
    from tail_absent
    group by posting_key, source_id, board_id, grp
    having count(*) >= {{ var('closure_consecutive_absences') }}
),

closed as (
    select
        posting_key,
        source_id,
        board_id,
        min(island_start) + ({{ var('closure_consecutive_absences') }} - 1) as closed_date
    from islands
    group by posting_key, source_id, board_id
)

select
    c.posting_key,
    c.closed_date,
    p.completed_at as closed_at,
    'absent_{{ var('closure_consecutive_absences') }}_snapshots' as closure_reason
from closed as c
inner join {{ ref('stg_snapshot_pointers') }} as p
    on c.source_id = p.source_id and c.board_id = p.board_id and c.closed_date = p.snapshot_date
