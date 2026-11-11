{% snapshot snap_postings %}
{# History of a posting's content/status as observed by the pipeline. Narrow on purpose: no
   description text, so history does not duplicate the largest column. #}
{{
    config(
        unique_key='posting_key',
        strategy='check',
        check_cols=['content_hash', 'source_status'],
    )
}}
select
    posting_key,
    source_id,
    company_id,
    title,
    content_hash,
    source_status,
    closed_at,
    closure_reason
from {{ ref('dim_postings') }}
{% endsnapshot %}
