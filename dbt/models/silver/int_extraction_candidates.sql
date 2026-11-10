-- Active postings in the target role families. The filter lives in config/extraction.yaml and
-- arrives as dbt vars; nothing is hard-coded here. title_family is a title heuristic used only for
-- stratified sampling, never as an extracted value.
{% set families = var('extraction_families') %}
{% set exclude = var('extraction_exclude') %}

with tagged as (
    select
        posting_key,
        source_id,
        company_id,
        company,
        title,
        location_raw,
        description_text,
        content_hash,
        case
            {% for f in families %}
            when regexp_matches(title, '{{ f.pattern }}', 'i')
                {% if f.exclude %}and not regexp_matches(title, '{{ f.exclude }}', 'i'){% endif %}
                then '{{ f.name }}'
            {% endfor %}
        end as title_family
    from {{ ref('dim_postings') }}
    where source_status = 'active'
)

select *
from tagged
where title_family is not null
    {% if exclude %}
    and not regexp_matches(title, '{{ exclude }}', 'i')
    {% endif %}
