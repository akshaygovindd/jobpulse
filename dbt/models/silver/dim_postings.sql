-- Silver postings: one row per posting_key with current content and lifecycle.
with latest as (
    select *
    from {{ ref('stg_raw_postings') }}
    qualify row_number() over (partition by posting_key order by snapshot_date desc) = 1
),

span as (
    select
        posting_key,
        min(snapshot_date) as first_seen_date,
        max(snapshot_date) as last_seen_date
    from {{ ref('stg_raw_postings') }}
    group by posting_key
),

pointers as (
    select * from {{ ref('stg_snapshot_pointers') }}
)

select
    l.posting_key,
    l.source_id,
    l.external_posting_id,
    l.board_id,
    p_last.company_id,
    p_last.company,
    l.title,
    l.location_raw,
    l.description_text,
    l.apply_url,
    l.content_hash,
    sp.first_seen_date,
    sp.last_seen_date,
    p_first.completed_at as first_seen_at,
    p_last.completed_at as last_seen_at,
    p_last.completed_at as last_confirmed_active_at,
    case when c.posting_key is null then 'active' else 'closed' end as source_status,
    c.closed_at,
    c.closure_reason,
    d.probable_duplicate_of
from latest as l
inner join span as sp on l.posting_key = sp.posting_key
inner join pointers as p_first
    on l.source_id = p_first.source_id
    and l.board_id = p_first.board_id
    and sp.first_seen_date = p_first.snapshot_date
inner join pointers as p_last
    on l.source_id = p_last.source_id
    and l.board_id = p_last.board_id
    and sp.last_seen_date = p_last.snapshot_date
left join {{ ref('int_posting_closure') }} as c on l.posting_key = c.posting_key
left join {{ ref('int_posting_duplicates') }} as d on l.posting_key = d.posting_key
