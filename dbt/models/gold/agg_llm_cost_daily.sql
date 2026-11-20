-- OpenAI extraction calls by day, model, mode and outcome (extract.llm_calls). Grain:
-- (call_date, model, mode, outcome). Cost is from token counts and the prices in
-- config/extraction.yaml at call time.
select
    cast(created_at as date) as call_date,
    model,
    mode,
    outcome,
    count(*) as n_calls,
    sum(input_tokens) as input_tokens,
    sum(output_tokens) as output_tokens,
    sum(cached_tokens) as cached_tokens,
    sum(cost_usd) as cost_usd
from {{ source('extract', 'llm_calls') }}
group by cast(created_at as date), model, mode, outcome
