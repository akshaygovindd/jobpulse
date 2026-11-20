-- Coverage of the fields the app relies on, over the current active, non-duplicate postings. Each
-- row is a share with its numerator and denominator so the app can show the counts, not just a
-- percentage. Grain: metric.
with p as (
    select * from {{ ref('fct_postings') }} where is_active and not is_probable_duplicate
)

select 'target_roles_analyzed' as metric,
    'open target data/AI role postings analyzed by the AI' as description,
    count(*) filter (where has_extraction) as numerator, count(*) as denominator
from p where is_target_role
union all
select 'extracted_share' as metric, 'active postings with an extraction' as description,
    count(*) filter (where has_extraction) as numerator, count(*) as denominator
from p
union all
select 'advertised_salary', 'extracted postings with an advertised salary',
    count(*) filter (where has_advertised_salary), count(*)
from p where has_extraction
union all
select 'salary_annualizable', 'postings with a salary that could be annualized',
    count(*) filter (where salary_min_annual is not null or salary_max_annual is not null),
    count(*) filter (where has_advertised_salary)
from p where has_extraction
union all
select 'city_parsed', 'active postings with a parsed city',
    count(*) filter (where city is not null), count(*)
from p
union all
select 'role_family_known', 'extracted postings with a role family other than other',
    count(*) filter (where role_family <> 'other'), count(*)
from p where has_extraction
union all
select 'sponsorship_stated', 'extracted postings that state a sponsorship position',
    count(*) filter (where sponsorship_statement in ('offered', 'not_offered')), count(*)
from p where has_extraction
