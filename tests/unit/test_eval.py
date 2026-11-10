"""Benchmark tooling: stratified allocation, BLIND sample sheet, and scoring."""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path

import pytest

from jobpulse.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "eval"))
common = importlib.import_module("common")
sample = importlib.import_module("sample")
score = importlib.import_module("score")


def test_allocate_is_exact_proportional_and_covers_every_stratum() -> None:
    sizes = {("a", "x"): 500, ("b", "x"): 300, ("c", "y"): 150, ("d", "y"): 3}
    alloc = sample.allocate(sizes, 200)
    assert sum(alloc.values()) == 200
    assert all(alloc[k] >= 1 for k in sizes)
    assert all(alloc[k] <= sizes[k] for k in sizes)
    assert alloc[("a", "x")] > alloc[("b", "x")] > alloc[("c", "y")]


def test_allocate_caps_at_population() -> None:
    assert sum(sample.allocate({("a", "x"): 4, ("b", "x"): 3}, 200).values()) == 7


def test_blind_sheet_columns_exclude_any_prediction_or_family() -> None:
    assert common.BLIND_COLUMNS == ["posting_key", "title", "company", "description_text"]
    forbidden = {"title_family", "model", "confidence", "content_hash", "status", "escalated"}
    assert not forbidden & set(common.BLIND_COLUMNS + common.LABEL_COLUMNS)


def test_sample_script_writes_blind_sheet_and_separate_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobpulse.ingest.storage import LocalStorage
    from tests.unit.test_extraction import D1, DESC
    from tests.warehouse_helpers import gh_job, ingest_day, run_pipeline

    storage = LocalStorage(tmp_path / "s3")
    jobs = [gh_job(str(i), title="Data Engineer", content=f"{DESC} #{i}") for i in range(12)]
    jobs += [gh_job("a1", title="Data Analyst", content=f"{DESC} analyst")]
    ingest_day(storage, D1, "greenhouse", {"acme": jobs})
    db = tmp_path / "wh.duckdb"
    _, silver = run_pipeline(storage, db)
    assert silver.success

    out = tmp_path / "labels_template.csv"
    manifest = tmp_path / "sample_manifest.json"
    monkeypatch.setattr(sample, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(sys, "argv", ["sample.py", "--db", str(db), "--out", str(out)])
    assert sample.main() == 0

    with out.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = list(reader.fieldnames or [])
    assert header == common.BLIND_COLUMNS + common.LABEL_COLUMNS
    assert len(rows) == 13  # fewer than 200 candidates: everything is taken
    assert all(r[c] == "" for r in rows for c in common.LABEL_COLUMNS)
    assert "gpt-" not in out.read_text(encoding="utf-8")
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert {i["posting_key"] for i in m["items"]} == {r["posting_key"] for r in rows}
    # redrawing is refused without --force (protects the owner's labels)
    assert sample.main() == 2


def _lab(key: str, **kw: str) -> dict[str, str]:
    base = dict.fromkeys(common.LABEL_COLUMNS, "")
    base.update({"posting_key": key, "done": "y"}, **kw)
    return base


def _pred(key: str, **kw: str) -> dict[str, str]:
    base = dict.fromkeys(common.PREDICTION_COLUMNS, "")
    base.update({"posting_key": key, "status": "extracted"}, **kw)
    return base


def test_scoring_precision_recall_and_coverage() -> None:
    labels = [
        _lab("a", seniority="senior", salary_min="100000", skills="SQL; dbt"),
        _lab("b", seniority="junior", salary_min="50000", skills="Python"),
        _lab("c", seniority="", skills=""),  # nothing stated
        _lab("d", seniority="mid"),  # quarantined: no prediction
        {**_lab("e", seniority="staff"), "done": ""},  # not finished: skipped
    ]
    preds = [
        _pred("a", seniority="senior", salary_min="100000", skills="sql;airflow"),
        _pred("b", seniority="senior", salary_min="60000", skills="python"),
        _pred("c", seniority="senior"),  # hallucinated value where label is empty
        {**_pred("d"), "status": "quarantined"},
    ]
    pairs, skipped = score.join(labels, preds)
    assert len(pairs) == 4
    assert skipped == 1
    f = score.score_fields(pairs)
    s = f["seniority"]  # preds: a ok, b wrong, c wrong; labels present: a, b, d
    assert (s["tp"], s["fp"], s["fn"]) == (1, 2, 2)
    assert s["precision"] == pytest.approx(1 / 3)
    assert s["recall"] == pytest.approx(1 / 3)
    sal = f["salary_min"]  # a matches; b wrong; d none
    assert (sal["tp"], sal["fp"], sal["fn"]) == (1, 1, 1)
    sk = f["skills"]  # tp: sql, python ; fp: airflow ; fn: dbt
    assert (sk["tp"], sk["fp"], sk["fn"]) == (2, 1, 1)


def test_sponsorship_report_is_separate_and_has_confusion_matrix() -> None:
    labels = [
        _lab("a", sponsorship_statement="offered"),
        _lab("b", sponsorship_statement="not_offered"),
        _lab("c", sponsorship_statement=""),
        _lab("d", sponsorship_statement="not_offered"),
    ]
    preds = [
        _pred("a", sponsorship_statement="offered"),
        _pred("b", sponsorship_statement="offered"),  # dangerous false positive
        _pred("c", sponsorship_statement="unspecified"),
        _pred("d", sponsorship_statement="not_offered"),
    ]
    pairs, _ = score.join(labels, preds)
    sp = score.score_sponsorship(pairs)
    assert sp["accuracy"] == pytest.approx(0.75)
    assert sp["matrix"]["not_offered->offered"] == 1
    assert sp["per_class"]["offered"]["precision"] == pytest.approx(0.5)
    assert sp["per_class"]["offered"]["low_support"] is True
    assert not any("sponsor" in k for k in score.score_fields(pairs))  # never mixed in


def test_score_main_writes_two_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lab, pred = tmp_path / "labels.csv", tmp_path / "pred.csv"
    for path, cols, rows in (
        (lab, common.BLIND_COLUMNS + common.LABEL_COLUMNS, [_lab("a", seniority="senior")]),
        (pred, common.PREDICTION_COLUMNS, [_pred("a", seniority="senior")]),
    ):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval="")
            w.writeheader()
            w.writerows(rows)
    out = tmp_path / "reports"
    argv = ["score.py", "--labels", str(lab), "--predictions", str(pred), "--out-dir", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    assert score.main() == 0
    main_md = (out / "extraction_report.md").read_text()
    assert "seniority" in main_md
    assert "| sponsorship" not in main_md
    assert "Sponsorship" in (out / "sponsorship_report.md").read_text()
