"""Benchmark tooling: stratified allocation, BLIND sample sheet, and scoring."""

from __future__ import annotations

import csv
import functools
import importlib
import json
import operator
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobpulse.config import REPO_ROOT, load_extraction
from jobpulse.extract.schema import parse_and_check

sys.path.insert(0, str(REPO_ROOT / "eval"))
common = importlib.import_module("common")
sample = importlib.import_module("sample")
sponsorship_sample = importlib.import_module("sponsorship_sample")
export_predictions = importlib.import_module("export_predictions")
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


# --- prefix-balanced order -------------------------------------------------------------------
def test_balanced_order_prefixes_are_proportional_and_deterministic() -> None:
    import random
    from collections import Counter

    groups = {
        ("a", "x"): list(range(100)),
        ("b", "x"): list(range(100, 160)),
        ("c", "y"): list(range(200, 240)),
    }
    order1 = sample.balanced_order(groups, random.Random(7))
    order2 = sample.balanced_order(groups, random.Random(7))
    assert order1 == order2
    assert sorted(order1) == sorted(functools.reduce(operator.iadd, groups.values(), []))
    owner = {i: k for k, v in groups.items() for i in v}
    for n in (20, 50, 100, 150):
        counts = Counter(owner[i] for i in order1[:n])
        for k, v in groups.items():
            assert abs(counts[k] - n * len(v) / 200) <= 1.5  # within about one row of proportional


# --- fixtures for the sample scripts ----------------------------------------------------------
SPONSOR_TEXT = "Our team builds data pipelines. We are unable to sponsor visas for this role."
PLAIN_TEXT = "Our team builds data pipelines with SQL and dbt, hybrid in NYC."


@pytest.fixture
def small_db(tmp_path: Path) -> Path:
    from jobpulse.ingest.storage import LocalStorage
    from tests.unit.test_extraction import D1
    from tests.warehouse_helpers import gh_job, ingest_day, run_pipeline

    storage = LocalStorage(tmp_path / "s3")
    jobs = []
    for i in range(10):
        jobs.append(gh_job(f"p{i}", title="Data Engineer", content=f"{PLAIN_TEXT} #{i}"))
        jobs.append(gh_job(f"s{i}", title="Data Analyst", content=f"{SPONSOR_TEXT} #{i}"))
    ingest_day(storage, D1, "greenhouse", {"acme": jobs})
    db = tmp_path / "wh.duckdb"
    _, silver = run_pipeline(storage, db)
    assert silver.success
    return db


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, main_size: int
) -> dict[str, Path]:
    paths = {
        "main": tmp_path / "labels_template.csv",
        "manifest": tmp_path / "sample_manifest.json",
        "sp": tmp_path / "sponsorship_labels_template.csv",
        "sp_manifest": tmp_path / "sponsorship_manifest.json",
    }
    cfg = sample.load_extraction()
    small = cfg.model_copy(update={"sample": cfg.sample.model_copy(update={"size": main_size})})
    monkeypatch.setattr(sample, "load_extraction", lambda: small)
    monkeypatch.setattr(sample, "MANIFEST_PATH", paths["manifest"])
    monkeypatch.setattr(sponsorship_sample, "MANIFEST_PATH", paths["manifest"])
    monkeypatch.setattr(sponsorship_sample, "SPONSOR_MANIFEST_PATH", paths["sp_manifest"])
    return paths


def _run(mod: object, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["x", *argv])
    return int(mod.main())  # type: ignore[attr-defined]


def test_sponsorship_sample_is_separate_blind_and_excludes_main(
    small_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _patch_paths(monkeypatch, tmp_path, main_size=8)
    assert _run(sample, monkeypatch, "--db", str(small_db), "--out", str(p["main"])) == 0
    main_keys = {r["posting_key"] for r in csv.DictReader(p["main"].open(encoding="utf-8"))}
    assert len(main_keys) == 8

    assert _run(sponsorship_sample, monkeypatch, "--db", str(small_db), "--out", str(p["sp"])) == 0
    with p["sp"].open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = list(reader.fieldnames or [])
    assert header == [*common.BLIND_COLUMNS, "sponsorship_statement"]  # only one label column
    assert all(r["sponsorship_statement"] == "" for r in rows)
    keys = {r["posting_key"] for r in rows}
    assert keys.isdisjoint(main_keys)  # nothing labeled twice
    assert all("sponsor" in r["description_text"].lower() for r in rows)
    m = json.loads(p["sp_manifest"].read_text(encoding="utf-8"))
    assert m["matching_candidates"] == 10
    assert m["size"] == len(rows) == 10 - m["already_in_main_sample"]
    assert m["size"] < m["requested"]  # fewer matches than requested: take what exists
    # the main sample is untouched and redraw is refused
    assert {r["posting_key"] for r in csv.DictReader(p["main"].open(encoding="utf-8"))} == main_keys
    assert _run(sponsorship_sample, monkeypatch, "--db", str(small_db), "--out", str(p["sp"])) == 2


def test_reorder_keeps_the_same_postings_and_refuses_after_labeling(
    small_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _patch_paths(monkeypatch, tmp_path, main_size=12)
    assert _run(sample, monkeypatch, "--db", str(small_db), "--out", str(p["main"])) == 0
    before = [r["posting_key"] for r in csv.DictReader(p["main"].open(encoding="utf-8"))]
    assert (
        _run(sample, monkeypatch, "--db", str(small_db), "--out", str(p["main"]), "--reorder") == 0
    )
    after = [r["posting_key"] for r in csv.DictReader(p["main"].open(encoding="utf-8"))]
    assert sorted(before) == sorted(after)
    m = json.loads(p["manifest"].read_text(encoding="utf-8"))
    assert [i["posting_key"] for i in m["items"]] == after  # manifest order follows the sheet
    # once a label exists, reordering is refused and the file is left alone
    text = p["main"].read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines(keepends=True)))
    rows[0]["seniority"] = "senior"
    with p["main"].open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    labeled = p["main"].read_text(encoding="utf-8")
    assert (
        _run(sample, monkeypatch, "--db", str(small_db), "--out", str(p["main"]), "--reorder") == 2
    )
    assert p["main"].read_text(encoding="utf-8") == labeled


def test_export_predictions_writes_separate_sponsorship_file(
    small_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobpulse.config import load_pipeline
    from jobpulse.extract.schema import prompt_version
    from jobpulse.extract.store import Store
    from tests.unit.test_extraction import valid_json

    p = _patch_paths(monkeypatch, tmp_path, main_size=8)
    assert _run(sample, monkeypatch, "--db", str(small_db), "--out", str(p["main"])) == 0
    assert _run(sponsorship_sample, monkeypatch, "--db", str(small_db), "--out", str(p["sp"])) == 0
    ecfg = load_extraction()
    import duckdb

    con = duckdb.connect(str(small_db))
    store = Store(con, load_pipeline().warehouse, ecfg, prompt_version(ecfg.schema_version))
    ex, _ = parse_and_check(
        valid_json(
            sponsorship_original_text="unable to sponsor visas",
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            salary_period=None,
            salary_original_text=None,
        ),
        SPONSOR_TEXT,
    )
    assert ex is not None
    sm = json.loads(p["sp_manifest"].read_text(encoding="utf-8"))
    first = store.posting_by_hash([sm["items"][0]["content_hash"]])[sm["items"][0]["content_hash"]]
    store.save_extraction(first, ex, "gpt-6-luna", False, datetime(2026, 11, 11, tzinfo=UTC))
    con.close()

    out_main, out_sp = tmp_path / "predictions.csv", tmp_path / "sponsorship_predictions.csv"
    monkeypatch.setattr(export_predictions, "MANIFEST_PATH", p["manifest"])
    monkeypatch.setattr(export_predictions, "SPONSOR_MANIFEST_PATH", p["sp_manifest"])
    monkeypatch.setattr(export_predictions, "SPONSOR_PREDICTIONS_PATH", out_sp)
    assert _run(export_predictions, monkeypatch, "--db", str(small_db), "--out", str(out_main)) == 0
    sp_rows = list(csv.DictReader(out_sp.open(encoding="utf-8")))
    assert list(sp_rows[0]) == common.SPONSOR_PREDICTION_COLUMNS
    assert [r["status"] for r in sp_rows].count("extracted") == 1
    assert sp_rows[0]["sponsorship_statement"] == "not_offered"


# --- scoring the targeted sample ---------------------------------------------------------------
def _write(path: Path, cols: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)


def _targeted_label(key: str, value: str) -> dict[str, str]:
    return {
        "posting_key": key,
        "title": "t",
        "company": "c",
        "description_text": "d",
        "sponsorship_statement": value,
    }


def _targeted_pred(key: str, value: str) -> dict[str, str]:
    return {
        "posting_key": key,
        "content_hash": key,
        "status": "extracted",
        "sponsorship_statement": value,
    }


def _score_with_targeted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    t_labels: list[dict[str, str]],
    t_preds: list[dict[str, str]],
) -> tuple[int, Path]:
    lab, pred = tmp_path / "labels.csv", tmp_path / "pred.csv"
    _write(
        lab,
        common.BLIND_COLUMNS + common.LABEL_COLUMNS,
        [_lab("a", seniority="senior", sponsorship_statement="offered")],
    )
    _write(
        pred,
        common.PREDICTION_COLUMNS,
        [_pred("a", seniority="senior", sponsorship_statement="offered")],
    )
    tl, tp = tmp_path / "sp_labels.csv", tmp_path / "sp_pred.csv"
    _write(tl, [*common.BLIND_COLUMNS, "sponsorship_statement"], t_labels)
    _write(tp, common.SPONSOR_PREDICTION_COLUMNS, t_preds)
    out = tmp_path / "reports"
    argv = [
        "score.py",
        "--labels",
        str(lab),
        "--predictions",
        str(pred),
        "--sponsorship-labels",
        str(tl),
        "--sponsorship-predictions",
        str(tp),
        "--out-dir",
        str(out),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return score.main(), out


def test_targeted_sample_feeds_only_the_sponsorship_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = [
        _targeted_label("t1", "not_offered"),
        _targeted_label("t2", "offered"),
        _targeted_label("t3", "unspecified"),
        _targeted_label("t4", ""),  # not labeled yet: skipped, not counted as unspecified
    ]
    preds = [
        _targeted_pred("t1", "not_offered"),
        _targeted_pred("t2", "unspecified"),
        _targeted_pred("t3", "unspecified"),
        _targeted_pred("t4", "offered"),
    ]
    code, out = _score_with_targeted(tmp_path, monkeypatch, labels, preds)
    assert code == 0
    sp = (out / "sponsorship_report.md").read_text()
    assert "## Targeted sample" in sp
    assert "## Pooled" in sp
    assert "Rows still unlabeled (blank): 1" in sp
    assert "Labeled postings: **3**" in sp  # targeted: t1, t2, t3
    assert "Labeled postings: **4**" in sp  # pooled: 'a' + 3
    main_md = (out / "extraction_report.md").read_text()
    assert "Labeled postings scored: **1**" in main_md  # targeted rows never enter the field report
    assert "Targeted sample" not in main_md


def test_targeted_sample_rejects_invalid_values_and_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _ = _score_with_targeted(
        tmp_path, monkeypatch, [_targeted_label("t1", "maybe")], [_targeted_pred("t1", "offered")]
    )
    assert code == 2
    code, _ = _score_with_targeted(
        tmp_path, monkeypatch, [_targeted_label("a", "offered")], [_targeted_pred("a", "offered")]
    )
    assert code == 2  # posting 'a' is already in the main labels
