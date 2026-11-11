-- (posting, skill): the extracted skills of each posting's current content, normalized and deduped.
with exploded as (
    select
        p.posting_key,
        unnest(e.skills) as raw_skill
    from {{ ref('dim_postings') }} as p
    inner join {{ ref('int_latest_extraction') }} as e on p.content_hash = e.content_hash
)

select distinct
    x.posting_key,
    sha256(m.canonical_name) as skill_id
from exploded as x
inner join {{ source('gold_stage', 'skill_map') }} as m on x.raw_skill = m.raw_skill
where m.canonical_name <> ''
