-- One extraction per content_hash: the most recent across prompt/schema versions. The extraction
-- cache key is (content_hash, schema_version, prompt_version), so several rows can exist per
-- content_hash after a prompt change. Gold uses the newest one.
select
    content_hash,
    posting_key as extracted_posting_key,
    model,
    escalated,
    skills,
    seniority,
    role_family,
    salary_min,
    salary_max,
    salary_currency,
    salary_period,
    coalesce(salary_period_inferred, false) as salary_period_inferred,
    remote_policy,
    sponsorship_statement,
    confidence,
    extracted_at
from {{ source('extract', 'extractions') }}
qualify row_number() over (
    partition by content_hash
    order by extracted_at desc, schema_version desc, prompt_version desc
) = 1
