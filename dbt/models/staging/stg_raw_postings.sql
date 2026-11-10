-- posting_key = hash(source_id ‖ external_posting_id), as in CLAUDE.md.
select
    sha256(source_id || chr(31) || external_posting_id) as posting_key,
    source_id,
    board_id,
    snapshot_date,
    attempt_id,
    external_posting_id,
    title,
    location_raw,
    description_text,
    apply_url,
    content_hash
from {{ source('bronze', 'raw_postings') }}
