-- OpenAI extraction calls by day, model, mode and outcome (extract.llm_calls). Grain:
-- (call_date, model, mode, outcome). Cost is from token counts and the prices in
-- config/extraction.yaml at call time. The day is the UTC date of the call (a bare cast of a
-- TIMESTAMPTZ uses the session time zone, so a build on a US laptop moved evening calls to the
-- previous day, before the data started).
select
    cast(created_at at time zone 'UTC' as date) as call_date,
    model,
    mode,
    outcome,
    count(*) as n_calls,
    sum(input_tokens) as input_tokens,
    sum(output_tokens) as output_tokens,
    sum(cached_tokens) as cached_tokens,
    sum(cost_usd) as cost_usd
from {{ source('extract', 'llm_calls') }}
group by cast(created_at at time zone 'UTC' as date), model, mode, outcome
