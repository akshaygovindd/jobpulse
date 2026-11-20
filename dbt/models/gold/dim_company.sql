-- One row per company, with visa evidence. Evidence from public filings, not a promise of
-- sponsorship. visa_evidence_class reflects FILING HISTORY only: HISTORICAL_EVIDENCE when the
-- employer has certified LCAs under a name matched at or above the evidence threshold, else
-- UNKNOWN. What a posting says is about that posting: it stays in fct_postings (sponsorship_class,
-- sponsorship_quote) and the company row only carries the counts of active postings that say
-- offered / not offered. Always show n_lca_certified next to the class. Sponsorship is a filter,
-- never part of a match score.
with postings as (
    select
        p.company_id,
        max(p.company) as name,
        list_sort(list(distinct p.source_id)) as sources,
        count(*) as n_postings,
        count(*) filter (where p.source_status = 'active') as n_active,
        count(*) filter (where f.has_extraction) as n_extracted,
        count(*) filter (where p.source_status = 'active'
            and f.sponsorship_statement = 'offered') as n_active_offered,
        count(*) filter (where p.source_status = 'active'
            and f.sponsorship_statement = 'not_offered') as n_active_not_offered
    from {{ ref('dim_postings') }} as p
    inner join {{ ref('fct_postings') }} as f on p.posting_key = f.posting_key
    group by p.company_id
)

select
    p.company_id,
    p.name,
    p.sources,
    p.n_postings,
    p.n_active,
    p.n_extracted,
    p.n_active_offered,
    p.n_active_not_offered,
    l.lca_match_confidence,
    l.lca_employer_names,
    l.lca_match_methods,
    coalesce(l.n_lca_certified, 0) as n_lca_certified,
    coalesce(l.n_lca_not_certified, 0) as n_lca_not_certified,
    l.lca_fiscal_years,
    case
        when coalesce(l.has_lca_evidence, false) then 'HISTORICAL_EVIDENCE'
        else 'UNKNOWN'
    end as visa_evidence_class
from postings as p
left join {{ ref('int_company_lca') }} as l on p.company_id = l.company_id
