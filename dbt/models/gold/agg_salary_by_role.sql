-- ADVERTISED salary only (never LCA wages), for active, extracted, non-duplicate postings, across
-- all cities. Same rules as agg_salary_by_role_city without the city dimension: most salary-bearing
-- postings are remote or list several cities and have no single city, so a city grain leaves them
-- in an "unspecified" bucket. Grain: (role_family, seniority, salary_currency); currencies are never
-- blended; a group with no salary has one row with currency 'none'.
--   n_postings, n_with_salary, coverage_pct : the whole (role, seniority) group, repeated on each
--       currency row of that group. Never sum them across currency rows.
--   n_currency_salary / n_annualized / p25/p50/p75 : as in agg_salary_by_role_city
{% set min_n = var('min_salaries_for_percentiles') %}

with postings as (
    select
        role_family,
        seniority,
        has_advertised_salary,
        salary_currency,
        (coalesce(salary_min_annual, salary_max_annual)
            + coalesce(salary_max_annual, salary_min_annual)) / 2 as midpoint_annual
    from {{ ref('fct_postings') }}
    where is_active and has_extraction and not is_probable_duplicate
),

grp as (
    select
        role_family,
        seniority,
        count(*) as n_postings,
        count(*) filter (where has_advertised_salary) as n_with_salary
    from postings
    group by role_family, seniority
),

cur as (
    select
        role_family,
        seniority,
        salary_currency,
        count(*) as n_currency_salary,
        count(midpoint_annual) as n_annualized,
        quantile_cont(midpoint_annual, 0.25) as q25,
        quantile_cont(midpoint_annual, 0.5) as q50,
        quantile_cont(midpoint_annual, 0.75) as q75
    from postings
    where has_advertised_salary and salary_currency is not null
    group by role_family, seniority, salary_currency
)

select
    g.role_family,
    g.seniority,
    coalesce(c.salary_currency, 'none') as salary_currency,
    g.n_postings,
    g.n_with_salary,
    100.0 * g.n_with_salary / g.n_postings as coverage_pct,
    coalesce(c.n_currency_salary, 0) as n_currency_salary,
    coalesce(c.n_annualized, 0) as n_annualized,
    case when c.n_annualized >= {{ min_n }} then c.q25 end as p25_annual,
    case when c.n_annualized >= {{ min_n }} then c.q50 end as p50_annual,
    case when c.n_annualized >= {{ min_n }} then c.q75 end as p75_annual
from grp as g
left join cur as c on g.role_family = c.role_family and g.seniority = c.seniority
