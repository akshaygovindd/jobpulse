-- Certified H-1B LCAs of matched employers, one row per case, with a role family from the job
-- title and an annualized offered wage. Only statuses in config/lca.yaml `certified_statuses`
-- (plain CERTIFIED) count as evidence; withdrawn and denied cases are counted separately in the
-- agg_ models. LCA wages are a separate dataset and are never blended with advertised salary.
{% set families = var('extraction_families') %}
{% set exclude = var('extraction_exclude') %}
{% set factors = var('annualization') %}

select
    d.case_number,
    d.company_id,
    d.fiscal_year,
    d.job_title,
    d.soc_code,
    d.soc_title,
    d.worksite_city,
    d.worksite_state,
    d.pw_wage_level,
    coalesce(d.total_worker_positions, 1) as n_positions,
    case
        {% for f in families %}
        when regexp_matches(d.job_title, '{{ f.pattern }}', 'i')
            {% if f.exclude %}and not regexp_matches(d.job_title, '{{ f.exclude }}', 'i'){% endif %}
            {% if exclude %}and not regexp_matches(d.job_title, '{{ exclude }}', 'i'){% endif %}
            then '{{ f.name }}'
        {% endfor %}
        else 'other'
    end as role_family,
    d.wage_rate_from * case d.wage_unit
        {% for period, factor in factors.items() %}
        when '{{ period | upper }}' then {{ factor }}
        {% endfor %}
    end as wage_from_annual,
    d.wage_rate_to * case d.wage_unit
        {% for period, factor in factors.items() %}
        when '{{ period | upper }}' then {{ factor }}
        {% endfor %}
    end as wage_to_annual
from {{ source('ref', 'lca_disclosures') }} as d
where d.case_status in (
    {% for s in var('lca_certified_statuses') %}'{{ s }}'{% if not loop.last %}, {% endif %}{% endfor %}
)
