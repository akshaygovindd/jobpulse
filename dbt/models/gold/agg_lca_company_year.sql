-- Public-filing evidence per company and fiscal year. Evidence from public filings, not a promise
-- of sponsorship. n_certified counts only plain CERTIFIED cases (config/lca.yaml); withdrawn after
-- certification, withdrawn and denied cases are shown separately and are not evidence.
-- Grain: (company_id, fiscal_year). A company with no filings has no rows here.
{% set cert = var('lca_certified_statuses') %}

select
    company_id,
    fiscal_year,
    count(*) filter (where case_status in (
        {% for s in cert %}'{{ s }}'{% if not loop.last %}, {% endif %}{% endfor %}
    )) as n_certified,
    coalesce(sum(coalesce(total_worker_positions, 1)) filter (where case_status in (
        {% for s in cert %}'{{ s }}'{% if not loop.last %}, {% endif %}{% endfor %}
    )), 0) as n_certified_positions,
    count(*) filter (where case_status = 'CERTIFIED - WITHDRAWN') as n_certified_withdrawn,
    count(*) filter (where case_status = 'WITHDRAWN') as n_withdrawn,
    count(*) filter (where case_status = 'DENIED') as n_denied,
    count(*) as n_cases
from {{ source('ref', 'lca_disclosures') }}
group by company_id, fiscal_year
