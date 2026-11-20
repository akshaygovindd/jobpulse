-- Certified LCAs per company, fiscal year and DOL prevailing-wage level (I lowest to IV highest;
-- 'unknown' where the filing has none). Evidence from public filings, not a promise of
-- sponsorship. Grain: (company_id, fiscal_year, wage_level).
select
    company_id,
    fiscal_year,
    coalesce(nullif(pw_wage_level, 'N/A'), 'unknown') as wage_level,
    count(*) as n_certified
from {{ ref('int_lca_certified') }}
group by 1, 2, 3
