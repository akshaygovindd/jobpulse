-- One row per posting (active and closed) with its extracted fields. Advertised salary only:
-- LCA wages are a separate dataset and are never joined here. Annual figures exist only where the
-- period is known (stated or inferred); `salary_period_inferred` says which.
{% set factors = var('annualization') %}

with ext as (
    select * from {{ ref('int_latest_extraction') }}
),

base as (
    select
        p.posting_key,
        p.company_id,
        p.source_id,
        p.title,
        p.location_raw,
        p.source_status,
        cast(p.first_seen_date as date) as first_seen_date,
        cast(p.closed_at as date) as closed_date,
        p.probable_duplicate_of,
        e.content_hash is not null as has_extraction,
        e.role_family,
        e.seniority,
        e.remote_policy,
        e.sponsorship_statement,
        e.salary_min,
        e.salary_max,
        e.salary_currency,
        e.salary_period,
        coalesce(e.salary_period_inferred, false) as salary_period_inferred,
        case e.salary_period
            {% for period, factor in factors.items() %}
            when '{{ period }}' then {{ factor }}
            {% endfor %}
        end as annual_factor
    from {{ ref('dim_postings') }} as p
    left join ext as e on p.content_hash = e.content_hash
)

select
    b.posting_key,
    b.company_id,
    b.source_id,
    b.title,
    b.has_extraction,
    b.role_family,
    b.seniority,
    b.remote_policy,
    lm.city,
    b.sponsorship_statement,
    b.salary_min,
    b.salary_max,
    b.salary_currency,
    b.salary_period,
    b.salary_period_inferred,
    b.salary_min * b.annual_factor as salary_min_annual,
    b.salary_max * b.annual_factor as salary_max_annual,
    (b.salary_min is not null or b.salary_max is not null) as has_advertised_salary,
    b.first_seen_date,
    b.closed_date,
    b.source_status = 'active' as is_active,
    b.probable_duplicate_of is not null as is_probable_duplicate
from base as b
left join {{ source('gold_stage', 'location_map') }} as lm on b.location_raw = lm.location_raw
