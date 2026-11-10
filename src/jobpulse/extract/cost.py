"""Token and dollar arithmetic for LLM calls (estimates before a call, actuals after)."""

from __future__ import annotations

import math

from jobpulse.config import CostModelConfig, ExtractionConfig, ModelConfig


def estimate_input_tokens(chars: int, cm: CostModelConfig) -> int:
    return math.ceil(chars / cm.chars_per_token) + cm.prompt_overhead_tokens


def call_cost(
    model: ModelConfig,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    *,
    batch_discount: float = 1.0,
) -> float:
    """Dollar cost of one call. Cached input is billed at the cached rate; reasoning tokens are
    assumed to be part of output_tokens."""
    uncached = max(0, input_tokens - cached_tokens)
    usd = (
        uncached * model.input_usd_per_mtok
        + cached_tokens * model.cached_input_usd_per_mtok
        + output_tokens * model.output_usd_per_mtok
    ) / 1_000_000
    return usd * batch_discount


def worst_case_cost(
    model: ModelConfig, chars: int, cfg: ExtractionConfig, *, batch_discount: float = 1.0
) -> float:
    """Upper bound used for the spend-cap check: estimated input plus max_output_tokens,
    times the configured safety factor."""
    cm = cfg.cost_model
    return cm.cap_safety_factor * call_cost(
        model,
        estimate_input_tokens(min(chars, cfg.max_description_chars), cm),
        cm.max_output_tokens,
        batch_discount=batch_discount,
    )


def projected_cost(
    n_postings: int, avg_chars: float, cfg: ExtractionConfig, *, batch: bool
) -> dict[str, float]:
    """Expected cost of extracting n postings (first pass + escalation share)."""
    cm = cfg.cost_model
    tokens_in = estimate_input_tokens(int(avg_chars), cm)
    discount = cfg.batch.discount if batch else 1.0
    first = n_postings * call_cost(
        cfg.first_pass, tokens_in, cm.expected_output_tokens, batch_discount=discount
    )
    # Escalations run synchronously (Standard price) after the batch completes.
    esc = (
        n_postings
        * cm.escalation_rate
        * call_cost(cfg.escalation, tokens_in, cm.expected_output_tokens)
    )
    return {"first_pass": first, "escalation": esc, "total": first + esc}
