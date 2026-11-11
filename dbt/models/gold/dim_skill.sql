-- One row per canonical skill name (normalization shared with eval/score.py: jobpulse.skills).
select
    sha256(canonical_name) as skill_id,
    canonical_name,
    count(distinct raw_skill) as n_spellings
from {{ source('gold_stage', 'skill_map') }}
where canonical_name <> ''
group by canonical_name
