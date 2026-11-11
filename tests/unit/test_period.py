"""Deterministic salary_period fill: the rule, the stored flag, the migration, and the reporting."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from typing import Any

import duckdb
import pytest

from jobpulse.config import REPO_ROOT, SalaryPeriodInferenceConfig, load_extraction, load_pipeline
from jobpulse.extract.period import infer_salary_period
from jobpulse.extract.store import Store
from tests.unit.test_extraction import FakeClient, go, resp, valid_json

sys.path.insert(0, str(REPO_ROOT / "eval"))
common = importlib.import_module("common")
score = importlib.import_module("score")

CFG = SalaryPeriodInferenceConfig(year_min=20000, hour_max=500)


# --- the rule ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("lo", "hi", "period", "expected"),
    [
        (120000, 150000, None, ("year", True)),
        (20000, None, None, ("year", True)),  # boundary: >= 20000 is year
        (19999.99, None, None, (None, False)),  # between: left empty
        (500, None, None, (None, False)),  # boundary: hour needs < 500
        (499.99, None, None, ("hour", True)),
        (45, 60, None, ("hour", True)),
        (None, 90000, None, ("year", True)),  # no minimum: falls back to the maximum
        (None, None, None, (None, False)),  # no amount: nothing to infer from
        (0, 0, None, (None, False)),  # a zero amount is not evidence of anything
        (120000, 150000, "year", ("year", False)),  # a period the model gave is never touched
        (50, 60, "year", ("year", False)),  # ...even when it looks wrong
        (3000, 4000, None, (None, False)),  # monthly-looking amounts are left alone
    ],
)
def test_rule(
    lo: float | None, hi: float | None, period: str | None, expected: tuple[Any, bool]
) -> None:
    assert infer_salary_period(lo, hi, period, CFG) == expected


def test_config_rejects_overlapping_thresholds() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        SalaryPeriodInferenceConfig(year_min=100, hour_max=500)


def test_live_config_matches_the_owner_decision() -> None:
    cfg = load_extraction().salary_period_inference
    assert (cfg.year_min, cfg.hour_max) == (20000, 500)


# --- stored at extraction time ---------------------------------------------------------------
def test_new_extraction_without_period_is_filled_and_flagged(store: Store, ecfg: Any) -> None:
    luna = ecfg.first_pass.id
    no_period = valid_json(salary_period=None)  # amount 120000-150000, period missing
    go(store, ecfg, FakeClient({luna: [resp(no_period)]}), limit=1)
    row = store.con.execute(
        f"SELECT salary_period, salary_period_inferred FROM {store.t('extractions')}"
    ).fetchone()
    assert row == ("year", True)
    # the model's own period is kept and not flagged
    go(
        store,
        ecfg,
        FakeClient(
            {
                luna: [
                    resp(
                        valid_json(
                            salary_period="hour",
                            salary_min=50,
                            salary_max=60,
                            salary_original_text="$120,000 - $150,000",
                        )
                    )
                ]
            }
        ),
    )
    rows = store.con.execute(
        f"SELECT salary_period, salary_period_inferred FROM {store.t('extractions')} ORDER BY 2"
    ).fetchall()
    assert ("hour", False) in rows


# --- migration of rows stored before the flag existed ------------------------------------------
LEGACY = """
    content_hash VARCHAR NOT NULL, schema_version INTEGER NOT NULL, prompt_version VARCHAR NOT NULL,
    posting_key VARCHAR NOT NULL, model VARCHAR NOT NULL, escalated BOOLEAN NOT NULL,
    skills VARCHAR[], seniority VARCHAR, role_family VARCHAR, salary_min DOUBLE, salary_max DOUBLE,
    salary_currency VARCHAR, salary_period VARCHAR, salary_original_text VARCHAR,
    remote_policy VARCHAR, sponsorship_statement VARCHAR, sponsorship_original_text VARCHAR,
    confidence DOUBLE, extracted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (content_hash, schema_version, prompt_version)
"""


def test_migration_fills_legacy_rows_once_and_is_idempotent() -> None:
    ecfg = load_extraction()
    con = duckdb.connect()
    con.execute('CREATE SCHEMA "extract"')
    con.execute(f'CREATE TABLE "extract"."extractions" ({LEGACY})')
    now = datetime(2026, 11, 1, tzinfo=UTC)
    base = [1, "v", "pk", "m", False, [], None, None]
    cases = [  # hash, min, max, period
        ("h-year", 120000, 150000, None),
        ("h-hour", 45, 60, None),
        ("h-mid", 3000, 4000, None),
        ("h-model", 80000, 90000, "year"),
        ("h-none", None, None, None),
    ]
    for h, lo, hi, per in cases:
        con.execute(
            'INSERT INTO "extract"."extractions" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [h, *base, lo, hi, "USD", per, None, None, None, None, 0.9, now],
        )
    store = Store(con, load_pipeline().warehouse, ecfg, "v")
    assert store.periods_inferred == 2  # only year and hour
    got = dict(
        (r[0], (r[1], r[2]))
        for r in con.execute(
            "SELECT content_hash, salary_period, salary_period_inferred "
            'FROM "extract"."extractions"'
        ).fetchall()
    )
    assert got == {
        "h-year": ("year", True),
        "h-hour": ("hour", True),
        "h-mid": (None, False),
        "h-model": ("year", False),
        "h-none": (None, False),
    }
    again = Store(con, load_pipeline().warehouse, ecfg, "v")
    assert again.periods_inferred == 0  # nothing left to fill; no flag is ever re-applied


# --- reporting ----------------------------------------------------------------------------------
def _lab(**kw: str) -> dict[str, str]:
    base = dict.fromkeys(common.LABEL_COLUMNS, "")
    base.update({"posting_key": "a", "done": "y"}, **kw)
    return base


def _pred(**kw: str) -> dict[str, str]:
    base = dict.fromkeys(common.PREDICTION_COLUMNS, "")
    base.update({"posting_key": "a", "status": "extracted"}, **kw)
    return base


def test_score_reports_pipeline_and_model_only_period_separately() -> None:
    labels = [
        _lab(posting_key="a", salary_period="year"),
        _lab(posting_key="b", salary_period="year"),
    ]
    preds = [
        _pred(posting_key="a", salary_period="year", salary_period_inferred="True"),  # filled
        _pred(posting_key="b", salary_period="year", salary_period_inferred="False"),  # model's own
    ]
    pairs, _ = score.join(labels, preds)
    f = score.score_fields(pairs)
    assert (f["salary_period"]["tp"], f["salary_period"]["fn"]) == (2, 0)
    assert (f["salary_period_model_only"]["tp"], f["salary_period_model_only"]["fn"]) == (1, 1)
    assert "model output only" in score.FIELD_LABELS["salary_period_model_only"]
