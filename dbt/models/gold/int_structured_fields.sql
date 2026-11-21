-- Workplace type and pay that the ATS itself states as structured fields (Ashby and Lever; Greenhouse
-- has none). These are the source of truth when present: gold prefers them over the AI's reading of
-- the description, which never sees them (bronze keeps only title, location, description, URL).
-- One row per (source_id, external_posting_id, content_hash). Raw payloads are in bronze.
with raw as (
    select source_id, external_posting_id, content_hash, payload
    from {{ source('bronze', 'posting_content') }}
    where source_id in ('ashby', 'lever') and payload is not null
),

ashby_salary as (
    -- the first Salary component among the first three (equity and bonus components are ignored)
    select
        source_id, external_posting_id, content_hash,
        try_cast(json_extract_string(comp, '$.minValue') as double) as pay_min,
        try_cast(json_extract_string(comp, '$.maxValue') as double) as pay_max,
        json_extract_string(comp, '$.currencyCode') as pay_currency,
        lower(split_part(json_extract_string(comp, '$.interval'), ' ', 2)) as pay_period,
        row_number() over (
            partition by source_id, external_posting_id, content_hash order by idx
        ) as rn
    from (
        select
            r.source_id, r.external_posting_id, r.content_hash, i.idx,
            json_extract(r.payload, '$.compensation.summaryComponents[' || i.idx || ']') as comp
        from raw as r
        cross join (select unnest([0, 1, 2]) as idx) as i
        where r.source_id = 'ashby'
    )
    where json_extract_string(comp, '$.compensationType') = 'Salary'
),

structured as (
    select
        r.source_id, r.external_posting_id, r.content_hash,
        case
            when r.source_id = 'ashby' then
                case lower(json_extract_string(r.payload, '$.workplaceType'))
                    when 'remote' then 'remote' when 'hybrid' then 'hybrid'
                    when 'onsite' then 'onsite' when 'on-site' then 'onsite'
                end
            else
                case lower(json_extract_string(r.payload, '$.workplaceType'))
                    when 'remote' then 'remote' when 'hybrid' then 'hybrid'
                    when 'onsite' then 'onsite' when 'on-site' then 'onsite'
                end
        end as remote_policy,
        case when r.source_id = 'ashby' then a.pay_min
            else try_cast(json_extract_string(r.payload, '$.salaryRange.min') as double) end as pay_min,
        case when r.source_id = 'ashby' then a.pay_max
            else try_cast(json_extract_string(r.payload, '$.salaryRange.max') as double) end as pay_max,
        case when r.source_id = 'ashby' then a.pay_currency
            else json_extract_string(r.payload, '$.salaryRange.currency') end as pay_currency,
        case
            when r.source_id = 'ashby' then a.pay_period
            when json_extract_string(r.payload, '$.salaryRange.interval') like '%year%' then 'year'
            when json_extract_string(r.payload, '$.salaryRange.interval') like '%month%' then 'month'
            when json_extract_string(r.payload, '$.salaryRange.interval') like '%week%' then 'week'
            when json_extract_string(r.payload, '$.salaryRange.interval') like '%hour%' then 'hour'
            when json_extract_string(r.payload, '$.salaryRange.interval') like '%day%' then 'day'
        end as pay_period
    from raw as r
    left join ashby_salary as a
        on r.source_id = a.source_id and r.external_posting_id = a.external_posting_id
        and r.content_hash = a.content_hash and a.rn = 1
)

select
    source_id, external_posting_id, content_hash, remote_policy,
    case when pay_period in ('year', 'month', 'week', 'hour', 'day')
        and pay_currency is not null and (pay_min is not null or pay_max is not null)
        then pay_min end as pay_min,
    case when pay_period in ('year', 'month', 'week', 'hour', 'day')
        and pay_currency is not null and (pay_min is not null or pay_max is not null)
        then pay_max end as pay_max,
    case when pay_period in ('year', 'month', 'week', 'hour', 'day')
        and pay_currency is not null then pay_currency end as pay_currency,
    case when pay_currency is not null and (pay_min is not null or pay_max is not null)
        then pay_period end as pay_period
from structured
