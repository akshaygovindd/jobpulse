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
]
