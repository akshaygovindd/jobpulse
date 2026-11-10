"""Labeling app: sheet I/O, validation, safe highlighting, blindness, and a headless UI run."""

from __future__ import annotations

import csv
import functools
import importlib
import operator
import re
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from jobpulse.config import REPO_ROOT

EVAL = REPO_ROOT / "eval"
sys.path.insert(0, str(EVAL))
common = importlib.import_module("common")
label_io = importlib.import_module("label_io")

MAIN_COLS = common.BLIND_COLUMNS + common.LABEL_COLUMNS
SP_COLS = [*common.BLIND_COLUMNS, "sponsorship_statement"]


def write_csv(path: Path, cols: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main_rows(n: int = 3) -> list[dict[str, str]]:
    return [
        {
            "posting_key": f"k{i}",
            "title": f"Data Engineer {i}",
            "company": f"Co{i}",
            "description_text": (
                f"Line one\nSalary $120,000 - $150,000. Hybrid. No visa sponsorship. <b>x{i}</b>"
            ),
        }
        for i in range(n)
    ]


@pytest.fixture
def sheets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    labels, sponsor = tmp_path / "labels.csv", tmp_path / "sponsorship_labels.csv"
    write_csv(labels, MAIN_COLS, main_rows(3))
    write_csv(sponsor, SP_COLS, main_rows(2))
    monkeypatch.setattr(common, "LABELS_PATH", labels)
    monkeypatch.setattr(common, "SPONSOR_LABELS_PATH", sponsor)
    return labels, sponsor


# --- pure logic -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$120,000", "120000"),
        ("120k", "120000"),
        (" 95.5 ", "95.5"),
        ("", ""),
        ("1,500K", "1500000"),
    ],
)
def test_parse_money(text: str, expected: str) -> None:
    assert label_io.parse_money(text) == (expected, None)


def test_parse_money_rejects_text() -> None:
    assert label_io.parse_money("about 100")[1] is not None
    assert label_io.parse_money("-5")[1] is not None


def test_parse_skills_normalizes_and_dedupes() -> None:
    assert label_io.parse_skills("SQL, dbt;\nsql\n Airflow ;;") == "SQL; dbt; Airflow"


def test_validate_main_salary_rules() -> None:
    base = dict.fromkeys(common.LABEL_COLUMNS, "")
    ok, errs = label_io.validate_main(
        {
            **base,
            "salary_min": "$100,000",
            "salary_max": "120k",
            "salary_currency": "usd",
            "salary_period": "year",
        }
    )
    assert not errs
    assert (ok["salary_min"], ok["salary_max"], ok["salary_currency"]) == (
        "100000",
        "120000",
        "USD",
    )
    _, errs = label_io.validate_main({**base, "salary_min": "100000"})
    assert any("currency" in e for e in errs)
    assert any("period" in e for e in errs)
    _, errs = label_io.validate_main(
        {
            **base,
            "salary_min": "9",
            "salary_max": "3",
            "salary_currency": "USD",
            "salary_period": "year",
        }
    )
    assert any("larger" in e for e in errs)
    _, errs = label_io.validate_main({**base, "salary_currency": "USD"})
    assert any("no salary amount" in e for e in errs)
    clean, errs = label_io.validate_main(base)  # a posting that states nothing is valid
    assert not errs
    assert clean["skills"] == ""


def test_validate_sponsorship_requires_a_choice() -> None:
    assert label_io.validate_sponsorship({"sponsorship_statement": ""})[1]
    assert label_io.validate_sponsorship({"sponsorship_statement": "maybe"})[1]
    assert label_io.validate_sponsorship({"sponsorship_statement": "unspecified"}) == (
        {"sponsorship_statement": "unspecified"},
        [],
    )


def test_enum_options_come_from_the_extraction_schema_without_unspecified() -> None:
    assert "unspecified" not in functools.reduce(operator.iadd, label_io.ENUM_OPTIONS.values(), [])
    assert "senior" in label_io.ENUM_OPTIONS["seniority"]
    assert label_io.ENUM_OPTIONS["sponsorship_statement"] == ["offered", "not_offered"]


def test_save_row_updates_only_that_row_and_keeps_a_backup(sheets: tuple[Path, Path]) -> None:
    labels, _ = sheets
    spec = label_io.build_specs(labels, sheets[1])[0]
    before = label_io.read_sheet(labels)
    label_io.save_row(spec, "k1", {"seniority": "senior", "skills": "SQL", "bogus": "x"})
    after = label_io.read_sheet(labels)
    assert after.fieldnames == before.fieldnames
    assert after.rows[1]["seniority"] == "senior"
    assert after.rows[1]["done"] == "y"
    assert after.rows[0] == before.rows[0]
    assert after.rows[2] == before.rows[2]
    assert all("bogus" not in r for r in after.rows)
    assert (
        after.rows[1]["description_text"] == before.rows[1]["description_text"]
    )  # newlines intact
    assert label_io.read_sheet(labels.with_name("labels.csv.bak")).rows == before.rows
    assert not list(labels.parent.glob("*.tmp"))
    with pytest.raises(KeyError):
        label_io.save_row(spec, "nope", {})


def test_save_row_does_not_clobber_edits_made_elsewhere(sheets: tuple[Path, Path]) -> None:
    labels, _ = sheets
    spec = label_io.build_specs(labels, sheets[1])[0]
    stale = label_io.read_sheet(labels)  # the app loaded this...
    other = label_io.read_sheet(labels)
    other.rows[2]["role_family"] = "bi"  # ...then row 2 changed on disk
    label_io.write_sheet(labels, other)
    assert stale.rows[2]["role_family"] == ""
    label_io.save_row(spec, "k0", {"seniority": "mid"})
    final = label_io.read_sheet(labels)
    assert final.rows[2]["role_family"] == "bi"
    assert final.rows[0]["seniority"] == "mid"


def test_progress_helpers(sheets: tuple[Path, Path]) -> None:
    labels, sponsor = sheets
    main, sp = label_io.build_specs(labels, sponsor)
    rows = label_io.read_sheet(labels).rows
    assert label_io.counts(main, rows) == (0, 0)
    assert label_io.next_unlabeled(main, rows, -1) == 0
    rows[0]["done"] = rows[1]["done"] = "y"
    assert label_io.counts(main, rows, prefix=2) == (2, 2)
    assert label_io.next_unlabeled(main, rows, 0) == 2
    assert label_io.next_unlabeled(main, rows, 2) == 2  # wraps to the only unlabeled row
    rows[2]["done"] = "y"
    assert label_io.next_unlabeled(main, rows, 0) is None
    srows = label_io.read_sheet(sponsor).rows
    srows[0]["sponsorship_statement"] = "unspecified"
    assert label_io.is_labeled(sp, srows[0])
    assert not label_io.is_labeled(sp, srows[1])


def test_highlight_escapes_untrusted_html_and_marks_keywords() -> None:
    out = label_io.highlight_html(
        "<script>alert(1)</script> Pay $120,000, Remote or hybrid; visa sponsorship, H-1B"
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    marks = re.findall(r"<mark[^>]*>(.*?)</mark>", out)
    assert {"$120,000", "Remote", "hybrid", "visa", "sponsorship", "H-1B"} <= set(marks)
    assert "<img" not in label_io.highlight_html("<img src=x onerror=alert(1)>")


# --- blindness ----------------------------------------------------------------------------------
def test_predictions_files_are_refused(tmp_path: Path) -> None:
    for name in ("predictions.csv", "sponsorship_predictions.csv"):
        p = tmp_path / name
        write_csv(p, MAIN_COLS, [])
        with pytest.raises(label_io.BlindnessError):
            label_io.read_sheet(p)
        with pytest.raises(label_io.BlindnessError):
            label_io.write_sheet(p, label_io.Sheet(MAIN_COLS, []))


def test_app_sources_never_reference_the_predictions_files() -> None:
    for name in ("label_app.py", "label_io.py"):
        text = (EVAL / name).read_text(encoding="utf-8")
        assert not re.search(
            r"PREDICTIONS?_PATH|PREDICTION_COLUMNS|predictions\.csv|export_predictions", text
        )


# --- headless UI --------------------------------------------------------------------------------
def run_app() -> AppTest:
    at = AppTest.from_file(str(EVAL / "label_app.py"), default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def progress_text(at: AppTest) -> str:
    return next(e.text for e in at.sidebar if e.type == "progress")


def click(at: AppTest, label: str) -> AppTest:
    next(b for b in at.button if b.label == label).click()
    return at.run()


def test_app_label_save_next_progress_and_edit(sheets: tuple[Path, Path]) -> None:
    labels, _ = sheets
    at = run_app()
    assert at.subheader[0].value == "Data Engineer 0"
    assert any("row 1 of 3" in m.value and "not labeled yet" in m.value for m in at.markdown)
    assert progress_text(at) == "Labeled 0 / 3  (0%)"
    assert any(
        "<mark" in m.value and "&lt;b&gt;" in m.value for m in at.markdown
    )  # highlighted + escaped

    at.selectbox(key="main::k0::seniority").select("senior")
    at.selectbox(key="main::k0::role_family").select("data_engineer")
    at.text_input(key="main::k0::min").input("$120,000")
    at.text_input(key="main::k0::max").input("150k")
    at.text_input(key="main::k0::cur").input("usd")
    at.selectbox(key="main::k0::per").select("year")
    at.text_area(key="main::k0::skills").input("SQL, dbt")
    at = click(at, "Save & Next")
    assert not at.exception
    assert not at.error
    saved = label_io.read_sheet(labels).rows[0]
    assert saved["done"] == "y"
    assert saved["seniority"] == "senior"
    assert (saved["salary_min"], saved["salary_max"], saved["salary_currency"]) == (
        "120000",
        "150000",
        "USD",
    )
    assert saved["skills"] == "SQL; dbt"
    assert at.subheader[0].value == "Data Engineer 1"  # moved on to the next unlabeled row
    assert progress_text(at) == "Labeled 1 / 3  (33%)"

    # a posting that states nothing: save with everything empty -> done=y
    at = click(at, "Save & Next")
    rows = label_io.read_sheet(labels).rows
    assert rows[1]["done"] == "y"
    assert rows[1]["seniority"] == ""
    assert at.subheader[0].value == "Data Engineer 2"
    assert progress_text(at) == "Labeled 2 / 3  (66%)"

    # jump back to row 1 and edit it
    at.sidebar.selectbox(key="jump::main").select(0)
    at.run()
    at.sidebar.selectbox(key="jump::main").select(0)
    assert at.subheader[0].value == "Data Engineer 0"
    assert at.selectbox(key="main::k0::seniority").value == "senior"  # prefilled from the CSV
    at.selectbox(key="main::k0::seniority").select("staff")
    at = click(at, "Save & Next")
    assert label_io.read_sheet(labels).rows[0]["seniority"] == "staff"


def test_app_validation_error_blocks_save(sheets: tuple[Path, Path]) -> None:
    labels, _ = sheets
    at = run_app()
    at.text_input(key="main::k0::min").input("100000")  # no currency / period
    at = click(at, "Save & Next")
    assert at.error
    assert any("currency" in e.value for e in at.error)
    assert label_io.read_sheet(labels).rows[0]["done"] == ""


def test_app_sponsorship_sheet_requires_explicit_choice(sheets: tuple[Path, Path]) -> None:
    _, sponsor = sheets
    at = run_app()
    at.sidebar.radio(key="sheet_choice").set_value(label_io.build_specs(*sheets)[1])
    at.run()
    assert at.subheader[0].value == "Data Engineer 0"
    at = click(at, "Save & Next")
    assert at.error  # blank is not allowed here
    at.selectbox(key="sponsorship::k0::sp").select("unspecified")
    at = click(at, "Save & Next")
    assert label_io.read_sheet(sponsor).rows[0]["sponsorship_statement"] == "unspecified"


def test_app_without_label_files_shows_copy_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "LABELS_PATH", tmp_path / "labels.csv")
    monkeypatch.setattr(common, "SPONSOR_LABELS_PATH", tmp_path / "sponsorship_labels.csv")
    at = run_app()
    assert at.error
    assert "cp eval/labels_template.csv eval/labels.csv" in at.code[0].value
