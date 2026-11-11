-- Weekly skill demand per role family, over extracted postings only (postings outside the target
-- role families are never extracted, so they are not in this denominator).
--   active_postings   : distinct postings seen in an authoritative snapshot that week
--   new_postings      : those first seen that week. In the first week of data every posting is
--                       "new" (history starts then), so read that week as a baseline, not a spike.
--   share_of_postings : active_postings / all extracted postings active that week in the role family
-- Cross-source probable duplicates are excluded so one job is not counted twice.
with seen as (
    select distinct
        o.posting_key,
        cast(date_trunc('week', o.snapshot_date) as date) as week_start,
        f.role_family,
        cast(date_trunc('week', f.first_seen_date) as date) as first_seen_week
    from {{ ref('fct_posting_observations') }} as o
    inner join {{ ref('fct_postings') }} as f on o.posting_key = f.posting_key
    where o.observed
        and f.has_extraction
        and not f.is_probable_duplicate
),

totals as (
    select week_start, role_family, count(distinct posting_key) as n_role_postings
    from seen
    group by week_start, role_family
)

select
    s.week_start,
    b.skill_id,
    s.role_family,
    count(distinct s.posting_key) as active_postings,
    count(distinct s.posting_key) filter (where s.first_seen_week = s.week_start) as new_postings,
    count(distinct s.posting_key) * 1.0 / t.n_role_postings as share_of_postings
from seen as s
inner join {{ ref('bridge_posting_skill') }} as b on s.posting_key = b.posting_key
inner join totals as t on s.week_start = t.week_start and s.role_family = t.role_family
group by s.week_start, b.skill_id, s.role_family, t.n_role_postings
