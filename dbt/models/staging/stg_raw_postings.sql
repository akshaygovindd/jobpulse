-- posting_key = hash(source_id ‖ external_posting_id), as in CLAUDE.md.
-- Description text lives once per (posting, content_hash) in bronze.posting_content; join it back so
-- downstream models still see one description per observation.
select
    sha256(r.source_id || chr(31) || r.external_posting_id) as posting_key,
    r.source_id,
    r.board_id,
    r.snapshot_date,
    r.attempt_id,
    r.external_posting_id,
    r.title,
    r.location_raw,
    c.description_text,
    r.apply_url,
    r.content_hash
from {{ source('bronze', 'raw_postings') }} as r
left join {{ source('bronze', 'posting_content') }} as c
    on r.source_id = c.source_id
    and r.external_posting_id = c.external_posting_id
    and r.content_hash = c.content_hash
