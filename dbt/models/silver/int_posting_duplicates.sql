-- Cross-source duplicate FLAG only (never merged). The same company + normalized title +
-- normalized location in a different source points at the earliest-seen posting.
-- Within one source, identity is already handled by posting_key.
with base as (
    select
        l.posting_key,
        l.source_id,
        c.company_id,
        trim(regexp_replace(lower(l.title), '[^a-z0-9]+', ' ', 'g')) as title_norm,
        trim(regexp_replace(lower(l.location_raw), '[^a-z0-9]+', ' ', 'g')) as location_norm,
        f.first_seen_date
    from (
        select * from {{ ref('stg_raw_postings') }}
        qualify row_number() over (partition by posting_key order by snapshot_date desc) = 1
    ) as l
    inner join (
        select posting_key, min(snapshot_date) as first_seen_date
        from {{ ref('stg_raw_postings') }}
        group by posting_key
    ) as f on l.posting_key = f.posting_key
    inner join (
        select distinct source_id, board_id, company_id from {{ ref('stg_snapshot_pointers') }}
    ) as c on l.source_id = c.source_id and l.board_id = c.board_id
),

candidates as (
    select
        b.posting_key,
        a.posting_key as original_key,
        row_number() over (
            partition by b.posting_key order by a.first_seen_date, a.posting_key
        ) as rn
    from base as b
    inner join base as a
        on a.company_id = b.company_id
        and a.title_norm = b.title_norm
        and a.location_norm = b.location_norm
        and a.source_id <> b.source_id
        and (a.first_seen_date, a.posting_key) < (b.first_seen_date, b.posting_key)
    where b.title_norm <> ''
)

select posting_key, original_key as probable_duplicate_of
from candidates
where rn = 1
