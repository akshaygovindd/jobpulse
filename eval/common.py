"""Shared constants for the benchmark scripts."""

from __future__ import annotations

from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = EVAL_DIR / "labels_template.csv"
LABELS_PATH = EVAL_DIR / "labels.csv"
MANIFEST_PATH = EVAL_DIR / "sample_manifest.json"
PREDICTIONS_PATH = EVAL_DIR / "predictions.csv"
REPORTS_DIR = EVAL_DIR / "reports"

# Blind labeling sheet: identifiers and posting text only, then empty label columns.
BLIND_COLUMNS = ["posting_key", "title", "company", "description_text"]
LABEL_COLUMNS = [
    "done",  # set to y once the row is fully labeled; only these rows are scored
    "seniority",
    "role_family",
    "remote_policy",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "sponsorship_statement",
    "skills",  # semicolon-separated
    "notes",
]
PREDICTION_COLUMNS = [
    "posting_key",
    "content_hash",
    "status",  # extracted | quarantined | missing
    "model",
    "escalated",
    "seniority",
    "role_family",
    "remote_policy",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "sponsorship_statement",
    "sponsorship_original_text",
    "skills",
    "salary_period_inferred",  # true when salary_period came from the deterministic rule
]

# Targeted sponsorship sample (separate files; used only by the sponsorship report).
SPONSOR_TEMPLATE_PATH = EVAL_DIR / "sponsorship_labels_template.csv"
SPONSOR_LABELS_PATH = EVAL_DIR / "sponsorship_labels.csv"
SPONSOR_MANIFEST_PATH = EVAL_DIR / "sponsorship_manifest.json"
SPONSOR_PREDICTIONS_PATH = EVAL_DIR / "sponsorship_predictions.csv"
# Here a blank cell means "not labeled yet"; write offered, not_offered or unspecified explicitly.
SPONSOR_LABEL_COLUMNS = ["sponsorship_statement"]
SPONSOR_VALUES = ("offered", "not_offered", "unspecified")
SPONSOR_PREDICTION_COLUMNS = [
    "posting_key",
    "content_hash",
    "status",
    "model",
    "sponsorship_statement",
    "sponsorship_original_text",
]
