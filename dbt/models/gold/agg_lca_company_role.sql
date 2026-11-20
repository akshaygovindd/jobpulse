-- Certified LCAs per company, fiscal year and role family (family from the LCA job title using the
-- same title rules as extraction; unmatched titles are 'other'). Evidence from public filings, not
-- a promise of sponsorship. Grain: (company_id, fiscal_year, role_family).
select
    company_id,
    fiscal_year,
    role_family,
    count(*) as n_certified,
    sum(n_positions) as n_positions
from {{ ref('int_lca_certified') }}
group by company_id, fiscal_year, role_family
