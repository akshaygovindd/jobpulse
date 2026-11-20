-- Per-company LCA evidence summary (match + certified counts). Shared by dim_company and
-- fct_postings so the evidence rule lives in one place. Evidence from public filings, not a
-- promise of sponsorship. has_lca_evidence: certified LCAs exist under a name matched at or above
-- the evidence threshold (config/lca.yaml).
with match as (
    select
        company_id,
        max(match_confidence) as lca_match_confidence,
        list_sort(list(distinct lca_employer_name)) as lca_employer_names,
        list_sort(list(distinct match_method)) as lca_match_methods
    from {{ source('ref', 'employer_lca_match') }}
    group by company_id
),

lca as (
    select
        company_id,
        sum(n_certified) as n_lca_certified,
        sum(n_cases - n_certified) as n_lca_not_certified,
        list_sort(list(distinct fiscal_year)) as lca_fiscal_years
    from {{ ref('agg_lca_company_year') }}
    group by company_id
)

select
    m.company_id,
    m.lca_match_confidence,
    m.lca_employer_names,
    m.lca_match_methods,
    coalesce(l.n_lca_certified, 0) as n_lca_certified,
    coalesce(l.n_lca_not_certified, 0) as n_lca_not_certified,
    l.lca_fiscal_years,
    coalesce(l.n_lca_certified, 0) > 0
        and m.lca_match_confidence >= {{ var('lca_evidence_threshold') }} as has_lca_evidence
from match as m
left join lca as l on m.company_id = l.company_id
