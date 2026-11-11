"""Deterministic fill for a missing salary_period. No model call; fully reproducible.

The model sometimes returns a salary amount without a period and validation accepts it. This rule
fills the gap from the magnitude of the amount and reports that it did, so inferred values can
always be told apart from what the model said.
"""

from __future__ import annotations

from jobpulse.config import SalaryPeriodInferenceConfig


def infer_salary_period(
    salary_min: float | None,
    salary_max: float | None,
    period: str | None,
    cfg: SalaryPeriodInferenceConfig,
) -> tuple[str | None, bool]:
    """Return (period, inferred). A period the model gave is never changed. Otherwise, using
    salary_min (or salary_max when there is no minimum): year when >= year_min, hour when
    0 < amount < hour_max, else left empty. A non-positive amount is never inferred from."""
    if period:
        return period, False
    amount = salary_min if salary_min is not None else salary_max
    if amount is None or amount <= 0:
        return None, False
    if amount >= cfg.year_min:
        return "year", True
    if amount < cfg.hour_max:
        return "hour", True
    return None, False
