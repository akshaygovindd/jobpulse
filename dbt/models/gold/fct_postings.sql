-- One row per posting (active and closed) with its extracted fields. Advertised salary only:
-- LCA wages are a separate dataset and are never joined here. Annual figures exist only where the
-- period is known (stated or inferred); `salary_period_inferred` says which.
{% set factors = var('annualization') %}
{% set hosts = var('apply_url_hosts') %}
{% set own = var('company_domains') %}

with ext as (
    select * from {{ ref('int_latest_extraction') }}
),

base as (
    select
        p.posting_key,
        p.company_id,
        p.source_id,
        p.title,
        p.apply_url,
        p.location_raw,
        p.source_status,
        cast(p.first_seen_date as date) as first_seen_date,
        cast(p.closed_at at time zone 'UTC' as date) as closed_date,
        p.probable_duplicate_of,
        e.content_hash is not null as has_extraction,
        e.role_family,
        e.seniority,
        -- the ATS's own structured workplace type and pay win over the AI's reading of the text
        coalesce(sf.remote_policy, e.remote_policy) as remote_policy,
        sf.remote_policy is not null as remote_from_ats,
        sf.pay_period is not null as salary_from_ats,
        e.sponsorship_statement,
        e.sponsorship_original_text,
        case when sf.pay_period is not null then sf.pay_min else e.salary_min end as salary_min,
        case when sf.pay_period is not null then sf.pay_max else e.salary_max end as salary_max,
        case when sf.pay_period is not null then sf.pay_currency else e.salary_currency end
            as salary_currency,
        case when sf.pay_period is not null then sf.pay_period else e.salary_period end
            as salary_period,
        case when sf.pay_period is not null then false
            else coalesce(e.salary_period_inferred, false) end as salary_period_inferred,
        case case when sf.pay_period is not null then sf.pay_period else e.salary_period end
            {% for period, factor in factors.items() %}
            when '{{ period }}' then {{ factor }}
            {% endfor %}
        end as annual_factor
    from {{ ref('dim_postings') }} as p
    left join ext as e on p.content_hash = e.content_hash
    -- structured fields apply only to analyzed postings (extracted fields stay NULL until then)
    left join {{ ref('int_structured_fields') }} as sf
        on e.content_hash is not null
        and p.source_id = sf.source_id
        and p.external_posting_id = sf.external_posting_id
        and p.content_hash = sf.content_hash
)

select
    b.posting_key,
    b.company_id,
    b.source_id,
    b.title,
    -- https only, and the host must be exactly an allow-listed ATS host (no credentials or port)
    case
        when b.apply_url like 'https://%'
            and split_part(b.apply_url, '/', 3) not like '%@%'
            and b.apply_url not similar to '.*[[:space:]"<>].*'
            and (
                split_part(b.apply_url, '/', 3) in ({% for h in hosts %}'{{ h }}'{{ ',' if not loop.last }}{% endfor %})
                -- the company's own career domain (config/employers.yaml `domains`) or a subdomain
                {% for cid, doms in own.items() %}
                or (b.company_id = '{{ cid }}' and (
                    {% for d in doms %}
                    split_part(b.apply_url, '/', 3) = '{{ d }}'
                    or split_part(b.apply_url, '/', 3) like '%.{{ d }}'
                    {{ 'or' if not loop.last }}
                    {% endfor %}
                ))
                {% endfor %}
            )
        then b.apply_url
    end as apply_url,
    b.has_extraction,
    tr.posting_key is not null as is_target_role,
    b.role_family,
    b.seniority,
    b.remote_policy,
    b.remote_from_ats,
    b.salary_from_ats,
    lm.city,
    lm.city_name,
    lm.city_state,
    lm.city_country,
    b.sponsorship_statement,
    b.sponsorship_original_text as sponsorship_quote,
    case
        when b.sponsorship_statement = 'offered' then 'CONFIRMED_OFFERED'
        when b.sponsorship_statement = 'not_offered' then 'EXPLICITLY_UNAVAILABLE'
        when coalesce(c.has_lca_evidence, false) then 'HISTORICAL_EVIDENCE'
        else 'UNKNOWN'
    end as sponsorship_class,
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
left join {{ ref('int_company_lca') }} as c on b.company_id = c.company_id
left join {{ ref('int_extraction_candidates') }} as tr on b.posting_key = tr.posting_key
