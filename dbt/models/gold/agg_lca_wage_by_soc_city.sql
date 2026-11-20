-- OFFERED wages on certified LCAs (the employer's stated pay on the filing, annualized from the
-- lower bound of the range). A separate dataset from advertised posting salary: never blended with
-- agg_salary_by_role_city. Only our matched employers' filings are stored, so this is not a market
-- wage survey. Percentiles are NULL below min_salaries_for_percentiles.
-- Grain: (role_family, worksite_state, worksite_city).
{% set min_n = var('min_salaries_for_percentiles') %}

select
    role_family,
    coalesce(worksite_state, 'unspecified') as worksite_state,
    coalesce(worksite_city, 'unspecified') as worksite_city,
    count(*) as n_certified,
    count(wage_from_annual) as n_with_wage,
    100.0 * count(wage_from_annual) / count(*) as coverage_pct,
    case when count(wage_from_annual) >= {{ min_n }}
        then quantile_cont(wage_from_annual, 0.25) end as p25_annual,
    case when count(wage_from_annual) >= {{ min_n }}
        then quantile_cont(wage_from_annual, 0.5) end as p50_annual,
    case when count(wage_from_annual) >= {{ min_n }}
        then quantile_cont(wage_from_annual, 0.75) end as p75_annual
from {{ ref('int_lca_certified') }}
group by 1, 2, 3
