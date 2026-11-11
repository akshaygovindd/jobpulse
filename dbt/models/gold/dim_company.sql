-- One row per company. Visa fields (LCA match, evidence class) arrive with the P5 loader.
select
    p.company_id,
    max(p.company) as name,
    list_sort(list(distinct p.source_id)) as sources,
    count(*) as n_postings,
    count(*) filter (where p.source_status = 'active') as n_active,
    count(*) filter (where f.has_extraction) as n_extracted
from {{ ref('dim_postings') }} as p
inner join {{ ref('fct_postings') }} as f on p.posting_key = f.posting_key
group by p.company_id
