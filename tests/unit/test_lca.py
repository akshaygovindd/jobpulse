"""LCA loader and employer matching, on synthetic DOL-shaped files (no real data, no network)."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from jobpulse import cli
from jobpulse.config import load_employers, load_lca, load_lca_aliases, load_pipeline
from jobpulse.visa.lca_loader import LcaLoadError, select_files
from jobpulse.visa.lca_loader import load_lca as run_load
from jobpulse.visa.match import NameRow, build_companies, build_matches, suggest
from jobpulse.visa.normalize import company_id_for, normalize_employer

LCA = load_lca()
WH = load_pipeline().warehouse
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
HEADER = [
    "CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "DECISION_DATE", "VISA_CLASS", "JOB_TITLE",
    "SOC_CODE", "SOC_TITLE", "FULL_TIME_POSITION", "TOTAL_WORKER_POSITIONS", "EMPLOYER_NAME",
    "EMPLOYER_FEIN", "EMPLOYER_POC_LAST_NAME", "AGENT_ATTORNEY_NAME", "WORKSITE_CITY",
    "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_TO", "WAGE_UNIT_OF_PAY",
    "PREVAILING_WAGE", "PW_UNIT_OF_PAY", "PW_WAGE_LEVEL",
]  # fmt: skip


def row(case: str, employer: str, **kw: str) -> dict[str, str]:
    base = {
        "CASE_NUMBER": case, "CASE_STATUS": "Certified", "RECEIVED_DATE": "2025-01-02",
        "DECISION_DATE": "2025-01-09", "VISA_CLASS": "H-1B", "JOB_TITLE": "Data Engineer",
        "SOC_CODE": "15-1252", "SOC_TITLE": "Software Developers", "FULL_TIME_POSITION": "Y",
        "TOTAL_WORKER_POSITIONS": "1", "EMPLOYER_NAME": employer, "EMPLOYER_FEIN": "12-3456789",
        "EMPLOYER_POC_LAST_NAME": "Secret", "AGENT_ATTORNEY_NAME": "Lawyer",
        "WORKSITE_CITY": "New York", "WORKSITE_STATE": "ny", "WAGE_RATE_OF_PAY_FROM": "$150,000.00",
        "WAGE_RATE_OF_PAY_TO": "160000", "WAGE_UNIT_OF_PAY": "Year", "PREVAILING_WAGE": "120,000",
        "PW_UNIT_OF_PAY": "Year", "PW_WAGE_LEVEL": "Level II",
    }  # fmt: skip
    base.update(kw)
    return base


def write_csv(path: Path, rows: list[dict[str, str]], header: list[str] | None = None) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header or HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


COMPANIES = build_companies(["Stripe", "Toast", "HubSpot", "Notion"], LCA.match)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    yield c  # type: ignore[misc]
    c.close()


def load(
    con: duckdb.DuckDBPyConnection,
    files: list[tuple[Path, int]],
    aliases: dict[str, list[str]] | None = None,
) -> Any:
    return run_load(con, WH, LCA, files, COMPANIES, aliases or {}, loaded_at=NOW)


def q(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    return con.execute(sql).fetchall()


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("Stripe, Inc.", "stripe"),
        ("TOAST INC", "toast"),
        ("Acme L.L.C.", "acme"),
        ("The Trade Desk, Inc.", "trade desk"),
        ("AT&T Services, Inc.", "at and t services"),
        ("Société Générale Corp", "societe generale"),
        ("Inc", "inc"),  # never normalized to nothing
        ("  Foo   Bar  Co.  ", "foo bar"),
    ],
)
def test_normalize(raw: str, norm: str) -> None:
    assert normalize_employer(raw, LCA.match.legal_suffixes) == norm


def test_company_id_matches_silver_slug() -> None:
    assert company_id_for("Trade Desk, Inc.") == "trade-desk-inc"
    assert company_id_for("HubSpot") == "hubspot"


def names(*items: tuple[str, int]) -> list[NameRow]:
    return [NameRow(r, normalize_employer(r, LCA.match.legal_suffixes), n) for r, n in items]


def test_match_methods_and_confidence() -> None:
    res = build_matches(
        names(("Stripe", 5), ("TOAST, INC.", 7), ("Stripe Payments Company", 40), ("Acme", 1)),
        COMPANIES,
        {"stripe": ["Stripe Payments Company"]},
        LCA.match,
    )
    by = {m.norm_name: m for m in res.matches}
    assert by["stripe"].method == "exact"
    assert by["toast"].method == "normalized"
    assert by["toast"].confidence == 0.9
    assert by["stripe payments"].method == "alias"
    assert by["stripe payments"].reviewed is True
    assert by["toast"].reviewed is False
    assert "acme" not in by  # nothing matches by accident


def test_no_fuzzy_match_only_suggestions() -> None:
    rows = names(("Stripe Payments Company", 40), ("Toasted Bagels LLC", 3), ("Random Co", 9))
    res = build_matches(rows, COMPANIES, {}, LCA.match)
    assert res.matches == []  # similar names are never matches on their own
    sugg = suggest(rows, COMPANIES, set(), LCA.match)
    pairs = {(s.company_id, s.norm_name) for s in sugg}
    assert ("stripe", "stripe payments") in pairs
    assert all(s.company_id != "notion" for s in sugg)


def test_ambiguous_name_is_not_matched() -> None:
    companies = build_companies(["Acme Corp", "ACME Inc"], LCA.match)
    res = build_matches(names(("ACME LLC", 3)), companies, {}, LCA.match)
    assert res.matches == []
    assert res.ambiguous == {"acme": ["acme-corp", "acme-inc"]}


def test_load_filters_dedups_and_drops_personal_columns(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    q3 = write_csv(
        tmp_path / "LCA_Disclosure_Data_FY2025_Q3.csv",
        [
            row("I-1", "STRIPE, INC.", CASE_STATUS="Certified - Withdrawn",
                DECISION_DATE="2025-03-01"),
            row("I-2", "TOAST INC"),
            row("I-3", "STRIPE, INC.", VISA_CLASS="E-3 Australian"),
            row("I-4", "STRIPE, INC.", VISA_CLASS="H-1B1 Chile"),
            row("I-5", "SOME OTHER CO"),
        ],
    )  # fmt: skip
    q4 = write_csv(
        tmp_path / "LCA_Disclosure_Data_FY2025_Q4.csv",
        [row("I-1", "STRIPE, INC.", DECISION_DATE="2025-04-01"), row("I-6", "HUBSPOT INC")],
    )
    result = load(con, [(q3, 2025), (q4, 2025)])
    assert result.rows_read == 7
    assert result.rows_kept_visa_class == 5  # E-3 and H-1B1 dropped
    assert result.cases == 4  # I-1 (duplicate), I-2, I-5, I-6
    assert result.companies_matched == 3
    rows = q(con, "SELECT case_number, company_id, case_status, soc_code, worksite_state, "
                  "wage_rate_from, wage_unit, pw_wage_level FROM ref.lca_disclosures "
                  "ORDER BY case_number")  # fmt: skip
    assert rows == [
        ("I-1", "stripe", "CERTIFIED", "15-1252", "NY", 150000.0, "YEAR", "LEVEL II"),
        ("I-2", "toast", "CERTIFIED", "15-1252", "NY", 150000.0, "YEAR", "LEVEL II"),
        ("I-6", "hubspot", "CERTIFIED", "15-1252", "NY", 150000.0, "YEAR", "LEVEL II"),
    ]  # the later decision (Q4, certified) replaced the Q3 withdrawn row
    cols = {r[0] for r in q(con, "SELECT column_name FROM information_schema.columns "
                                 "WHERE table_schema = 'ref'")}  # fmt: skip
    assert not {c for c in cols if "fein" in c or "poc" in c or "attorney" in c or "agent" in c}
    # unmatched employers are not stored as filings, but their names are, for review
    assert q(
        con, "SELECT count(*) FROM ref.lca_employer_names WHERE employer_name = 'SOME OTHER CO'"
    ) == [(1,)]
    assert q(con, "SELECT count(*) FROM ref.lca_disclosures WHERE case_number = 'I-5'") == [(0,)]
    assert q(con, "SELECT match_method, match_confidence, reviewed FROM ref.employer_lca_match "
                  "WHERE company_id = 'toast'") == [("normalized", 0.9, False)]  # fmt: skip


def test_excel_style_dates_and_numbers(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    f = write_csv(
        tmp_path / "LCA_Disclosure_Data_FY2024_Q4.csv",
        [row("S-1", "TOAST INC", RECEIVED_DATE="45292", DECISION_DATE="45300",
             WAGE_RATE_OF_PAY_FROM="85.5", WAGE_UNIT_OF_PAY="Hour")],
    )  # fmt: skip
    load(con, [(f, 2024)])
    got = q(
        con,
        "SELECT received_date, decision_date, wage_rate_from, wage_unit FROM ref.lca_disclosures",
    )
    assert got == [(date(2024, 1, 1), date(2024, 1, 9), 85.5, "HOUR")]


def test_missing_required_column_fails_loudly(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    f = write_csv(
        tmp_path / "LCA_Disclosure_Data_FY2025_Q4.csv",
        [row("X-1", "TOAST INC")],
        header=[h for h in HEADER if h != "SOC_CODE"],
    )
    with pytest.raises(LcaLoadError, match=r"missing required columns \['soc_code'\]"):
        load(con, [(f, 2025)])
    assert q(con, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'ref'") == [
        (0,)
    ]


def test_header_variants_are_accepted(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    header = [h.replace("WAGE_RATE_OF_PAY_FROM", "wage_rate_of_pay_from_1") for h in HEADER]
    r = row("V-1", "TOAST INC")
    r["wage_rate_of_pay_from_1"] = r.pop("WAGE_RATE_OF_PAY_FROM")
    f = write_csv(tmp_path / "LCA_Disclosure_Data_FY2025_Q4.csv", [r], header=header)
    load(con, [(f, 2025)])
    assert q(con, "SELECT wage_rate_from FROM ref.lca_disclosures") == [(150000.0,)]


def test_reload_is_idempotent_and_replaces_only_its_year(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    f24 = write_csv(tmp_path / "LCA_Disclosure_Data_FY2024_Q4.csv", [row("A-1", "TOAST INC")])
    f25 = write_csv(tmp_path / "LCA_Disclosure_Data_FY2025_Q4.csv", [row("B-1", "TOAST INC")])
    load(con, [(f24, 2024), (f25, 2025)])
    first = q(con, "SELECT case_number, fiscal_year FROM ref.lca_disclosures ORDER BY 1")
    load(con, [(f24, 2024), (f25, 2025)])
    assert q(con, "SELECT case_number, fiscal_year FROM ref.lca_disclosures ORDER BY 1") == first
    f25b = write_csv(tmp_path / "LCA_Disclosure_Data_FY2025_Q4.csv", [row("B-2", "TOAST INC")])
    load(con, [(f25b, 2025)])  # only FY2025 reloaded: FY2024 stays, FY2025 is replaced
    assert q(con, "SELECT case_number, fiscal_year FROM ref.lca_disclosures ORDER BY 1") == [
        ("A-1", 2024),
        ("B-2", 2025),
    ]


def test_adding_an_alias_and_reloading_adds_the_filings(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    f = write_csv(
        tmp_path / "LCA_Disclosure_Data_FY2025_Q4.csv",
        [row("P-1", "STRIPE PAYMENTS COMPANY"), row("P-2", "STRIPE, INC.")],
    )
    load(con, [(f, 2025)])
    assert q(con, "SELECT case_number FROM ref.lca_disclosures") == [("P-2",)]
    load(con, [(f, 2025)], aliases={"stripe": ["Stripe Payments Company"]})
    assert q(con, "SELECT case_number, company_id FROM ref.lca_disclosures ORDER BY 1") == [
        ("P-1", "stripe"),
        ("P-2", "stripe"),
    ]
    assert q(con, "SELECT match_method, reviewed FROM ref.employer_lca_match "
                  "WHERE lca_employer_name = 'stripe payments'") == [("alias", True)]  # fmt: skip


def test_xlsx_files_load_like_csv(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    try:
        con.execute("INSTALL excel")
        con.execute("LOAD excel")
    except duckdb.Error:
        pytest.skip("DuckDB excel extension not available offline")
    csv_path = write_csv(tmp_path / "src.csv", [row("X-1", "TOAST INC")])
    xlsx = tmp_path / "LCA_Disclosure_Data_FY2025_Q4.xlsx"
    con.execute(f"COPY (SELECT * FROM read_csv('{csv_path}', all_varchar=true)) "
                f"TO '{xlsx}' (FORMAT xlsx, HEADER true)")  # fmt: skip
    target = duckdb.connect(":memory:")
    run_load(target, WH, LCA, [(xlsx, 2025)], COMPANIES, {}, loaded_at=NOW)
    assert q(target, "SELECT case_number, company_id, wage_rate_from FROM ref.lca_disclosures") == [
        ("X-1", "toast", 150000.0)
    ]


def test_select_files_by_fiscal_year(tmp_path: Path) -> None:
    for name in (
        "LCA_Disclosure_Data_FY2025_Q4.xlsx",
        "LCA_Disclosure_Data_FY2023_Q4.xlsx",
        "notes.txt",
        "~$lock.xlsx",
        "LCA_Record_Layout_FY2025.pdf",
    ):
        (tmp_path / name).write_bytes(b"")
    files, skipped = select_files(tmp_path, LCA)
    assert [(p.name, fy) for p, fy in files] == [("LCA_Disclosure_Data_FY2025_Q4.xlsx", 2025)]
    assert skipped == ["LCA_Disclosure_Data_FY2023_Q4.xlsx"]


def test_no_files_is_an_error(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(LcaLoadError, match="no LCA files"):
        load(con, [])


def test_cli_load_lca_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = tmp_path / "oflc"
    d.mkdir()
    write_csv(d / "LCA_Disclosure_Data_FY2025_Q4.csv", [row("C-1", "HUBSPOT, INC.")])
    db = tmp_path / "wh.duckdb"
    assert cli.main(["load-lca", "--dir", str(d), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "not a promise of sponsorship" in out
    c = duckdb.connect(str(db))
    assert q(c, "SELECT company_id, fiscal_year FROM ref.lca_disclosures") == [("hubspot", 2025)]
    assert q(c, "SELECT task, source_id, status FROM ops.pipeline_runs") == [
        ("lca", "oflc", "success")
    ]
    assert cli.main(["load-lca", "--dir", str(tmp_path / "missing"), "--db", str(db)]) == 2


def test_alias_config_names_real_companies() -> None:
    ids = {
        c.company_id
        for c in build_companies((e.company for e in load_employers().employers), LCA.match)
    }
    assert set(load_lca_aliases().aliases) <= ids
    assert 0 < LCA.match.threshold <= 1
    assert LCA.match.confidence["normalized"] >= LCA.match.threshold  # normalized matches count
