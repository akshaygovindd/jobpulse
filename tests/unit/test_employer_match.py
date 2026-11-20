"""Employer-match review sheet (blind) and precision scoring."""

from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from jobpulse.config import load_lca, load_pipeline
from jobpulse.visa.lca_loader import ensure_ref_tables

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval"))

import employer_match as em  # noqa: E402
import employer_match_sample as sample  # noqa: E402

LCA = load_lca()
WH = load_pipeline().warehouse
NOW = datetime(2026, 11, 20, tzinfo=UTC)
COMPANIES = ["Stripe", "Toast", "HubSpot", "Notion"]


NAMES = [
    ("STRIPE, INC.", "stripe", 50),
    ("TOAST INC", "toast", 20),
    ("HUBSPOT, INC.", "hubspot", 9),
    ("STRIPE PAYMENTS COMPANY", "stripe payments", 30),
    ("TOASTED BAGELS LLC", "toasted bagels", 2),
    ("NOTION LABS INC", "notion labs", 6),
    ("HUBSPOT HOLDINGS", "hubspot holdings", 1),
    ("UNRELATED CORP", "unrelated", 99),
]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    ensure_ref_tables(c, WH)
    for fy in (2024, 2025):
        for raw, norm, n in NAMES:
            c.execute(
                "INSERT INTO ref.lca_employer_names VALUES (?,?,?,?,?)", [fy, raw, norm, n, n]
            )
    for cid, norm, ex, m, conf in [
        ("stripe", "stripe", "STRIPE, INC.", "normalized", 0.9),
        ("toast", "toast", "TOAST INC", "normalized", 0.9),
        ("hubspot", "hubspot", "HUBSPOT, INC.", "normalized", 0.9),
    ]:
        c.execute(
            "INSERT INTO ref.employer_lca_match (company_id, lca_employer_name, example_name, "
            "match_method, match_confidence, reviewed, n_cases, matched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [cid, norm, ex, m, conf, False, 10, NOW],
        )
    return c


def pairs_for(con: duckdb.DuckDBPyConnection, size: int = 8) -> list[em.Pair]:
    return em.build_pairs(con, WH, LCA, COMPANIES, size=size)


def test_pairs_mix_matches_and_near_misses_without_leaking_the_difference(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    pairs = pairs_for(con)
    origins = {p.origin for p in pairs}
    assert origins == {"matched", "suggested"}
    suggested = {p.norm_name for p in pairs if p.origin == "suggested"}
    assert suggested <= {"stripe payments", "toasted bagels", "hubspot holdings", "notion labs"}
    assert "unrelated" not in suggested  # not similar to any company
    assert not suggested & {"stripe", "toast", "hubspot"}  # matched names are never "suggested"
    sheet, manifest = tmp_path / "s.csv", tmp_path / "m.json"
    em.write_sheet(pairs, sheet, manifest, 1)
    rows = list(csv.DictReader(sheet.open()))
    assert list(rows[0]) == em.SHEET_COLUMNS  # blind: no origin, method, confidence, similarity
    assert all(r["label"] == "" for r in rows)
    assert len({r["pair_id"] for r in rows}) == len(rows)
    meta = json.loads(manifest.read_text())["pairs"]
    assert {p["origin"] for p in meta} == {"matched", "suggested"}
    assert all(p["method"] for p in meta if p["origin"] == "matched")


def test_pairs_are_deterministic(con: duckdb.DuckDBPyConnection) -> None:
    assert pairs_for(con) == pairs_for(con)


def test_sample_refuses_to_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "s.csv").write_text("x")
    monkeypatch.setattr(sample, "SHEET", tmp_path / "s.csv")
    monkeypatch.setattr(sample, "MANIFEST", tmp_path / "m.json")
    assert sample.main(["--db", str(tmp_path / "none.duckdb")]) == 2
    assert "already exists" in capsys.readouterr().err


def manifest_of(pairs: list[em.Pair]) -> dict[str, object]:
    return {"pairs": [{k: v for k, v in p.__dict__.items()} for p in pairs]}


def test_score_precision_by_method_and_missed_matches(con: duckdb.DuckDBPyConnection) -> None:
    pairs = pairs_for(con, size=8)
    matched = [p for p in pairs if p.origin == "matched"]
    suggested = [p for p in pairs if p.origin == "suggested"]
    labels = {matched[0].pair_id: "yes", matched[1].pair_id: "yes", matched[2].pair_id: "no"}
    labels[suggested[0].pair_id] = "yes"
    for p in suggested[1:]:
        labels[p.pair_id] = "no"
    result = em.score(labels, manifest_of(pairs), target=4)
    assert result["matched"]["n"] == 3
    assert result["matched"]["precision"] == pytest.approx(2 / 3)
    lo, hi = result["matched"]["ci95"]
    assert 0 < lo < 2 / 3 < hi < 1
    assert result["by_method"]["normalized"]["yes"] == 2
    assert [p["pair_id"] for p in result["matched_wrong"]] == [matched[2].pair_id]
    assert [p["pair_id"] for p in result["suggested_yes"]] == [suggested[0].pair_id]
    assert result["meets_target"] is True
    text = em.render(result)
    assert "not a promise of sponsorship" in text
    assert "66.7%" in text


def test_unsure_is_not_counted_and_target_flag(con: duckdb.DuckDBPyConnection) -> None:
    pairs = pairs_for(con)
    matched = [p for p in pairs if p.origin == "matched"]
    labels = {matched[0].pair_id: "unsure", matched[1].pair_id: "yes"}
    result = em.score(labels, manifest_of(pairs))
    assert result["matched"]["n"] == 1
    assert result["matched"]["unsure"] == 1
    assert result["meets_target"] is False  # 2 of the required 50
    assert "NOT met" in em.render(result)


def test_bad_labels_are_rejected(tmp_path: Path, con: duckdb.DuckDBPyConnection) -> None:
    f = tmp_path / "l.csv"
    f.write_text("pair_id,company,lca_employer_name,n_cases_all_years,label\nP001,x,y,1,maybe\n")
    with pytest.raises(ValueError, match="yes, no or unsure"):
        em.read_labels(f)
    with pytest.raises(ValueError, match="unknown pairs"):
        em.score({"P999": "yes"}, manifest_of(pairs_for(con)))


def test_no_matches_gives_no_precision() -> None:
    result = em.score({}, {"pairs": []})
    assert result["matched"]["precision"] is None
    assert "n/a" in em.render(result)
