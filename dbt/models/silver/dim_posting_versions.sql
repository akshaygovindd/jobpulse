-- A new row only when content_hash changes between consecutive observations of a posting.
with observed as (
    select
        s.posting_key,
        s.snapshot_date,
        s.content_hash,
        s.title,
        s.location_raw,
        s.description_text,
        s.apply_url,
        lag(s.content_hash) over (partition by s.posting_key order by s.snapshot_date) as prev_hash
    from {{ ref('stg_raw_postings') }} as s
),

changes as (
    select * from observed
    where prev_hash is null or prev_hash <> content_hash
)

select
    posting_key,
    content_hash,
    snapshot_date as valid_from,
    lead(snapshot_date) over (partition by posting_key order by snapshot_date) as valid_to,
    title,
    location_raw,
    description_text,
    apply_url
from changes
