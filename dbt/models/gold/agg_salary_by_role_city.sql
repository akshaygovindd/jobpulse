-- ADVERTISED salary only (never LCA wages), for active, extracted, non-duplicate postings.
-- Grain: (role_family, seniority, city, salary_currency). One row per currency seen in the group,
-- so currencies are never blended; a group with no salary at all has one row with currency 'none'.
--   n_postings, n_with_salary, coverage_pct : the whole (role, seniority, city) group, repeated on
--       each currency row of that group. Never sum them across currency rows.
--   n_currency_salary : postings in the group advertising a salary in this currency
--   n_annualized      : of those, the ones with a known period (stated or inferred)
--   p25/p50/p75       : of the annualized midpoint, NULL below min_salaries_for_percentiles
{% set min_n = var('min_salaries_for_percentiles') %}

with postings as (
    select
        role_family,
        seniority,
        coalesce(city, 'unspecified') as city,
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
        city,
        count(*) as n_postings,
        count(*) filter (where has_advertised_salary) as n_with_salary
    from postings
    group by role_family, seniority, city
),

cur as (
    select
        role_family,
        seniority,
        city,
        salary_currency,
        count(*) as n_currency_salary,
        count(midpoint_annual) as n_annualized,
        quantile_cont(midpoint_annual, 0.25) as q25,
        quantile_cont(midpoint_annual, 0.5) as q50,
        quantile_cont(midpoint_annual, 0.75) as q75
    from postings
    where has_advertised_salary and salary_currency is not null
    group by role_family, seniority, city, salary_currency
)

select
    g.role_family,
    g.seniority,
    g.city,
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
left join cur as c
    on g.role_family = c.role_family and g.seniority = c.seniority and g.city = c.city
