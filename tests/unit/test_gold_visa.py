"""Gold visa tables: LCA evidence per company, status handling, role family, wage, evidence class.

Evidence from public filings, not a promise of sponsorship. Ref rows are written by hand (no DOL
files, no network); silver comes from synthetic postings of company "acme".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from jobpulse.config import load_pipeline
from jobpulse.ingest.storage import LocalStorage
from jobpulse.visa.lca_loader import ensure_ref_tables
from tests.unit.test_gold import add_extraction, fct, gold, seed
from tests.warehouse_helpers import gh_job, query

D1 = date(2026, 11, 10)
NOW = datetime(2026, 11, 20, tzinfo=UTC)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "s3")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "wh.duckdb"


def lca_case(
    n: int,
    *,
    status: str = "CERTIFIED",
    fy: int = 2025,
    title: str = "Data Engineer",
    level: str | None = "II",
    wage: float = 100000.0,
    unit: str = "YEAR",
    positions: int = 1,
) -> tuple[object, ...]:
    return (
        f"I-200-{n:05d}", fy, "f.xlsx", status, None, None, "H-1B", "Acme, Inc.", "acme", "acme",
        title, "15-1252.00", "Software Developers", "Y", positions, "New York", "NY",
        wage, wage + 10000, unit, 90000.0, "YEAR", level, NOW,
    )  # fmt: skip


def load_ref(db: Path, cases: Sequence[tuple[object, ...]], confidence: float = 0.9) -> None:
    con = duckdb.connect(str(db))
    try:
        ensure_ref_tables(con, load_pipeline().warehouse)
        for c in cases:
            con.execute(
                f"INSERT INTO ref.lca_disclosures VALUES ({','.join('?' * len(c))})", list(c)
            )
        con.execute(
            "INSERT INTO ref.employer_lca_match (company_id, lca_employer_name, example_name, "
            "match_method, match_confidence, reviewed, n_cases, matched_at) "
            "VALUES ('acme','acme','Acme, Inc.','normalized',?,false,?,?)",
            [confidence, len(cases), NOW],
        )
    finally:
        con.close()


def company(db: Path) -> tuple[object, ...]:
    return query(
        db,
        "SELECT visa_evidence_class, n_lca_certified, n_lca_not_certified, lca_match_confidence "
        "FROM gold.dim_company",
    )[0]


def test_subsidiary_names_reach_dim_company(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    load_ref(db, [lca_case(1)])
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "INSERT INTO ref.employer_lca_match VALUES "
            "('acme','acme payments','Acme Payments, LLC','alias',1.0,true,2,?,'subsidiary')",
            [NOW],
        )
    finally:
        con.close()
    gold(db)
    assert query(db, "SELECT lca_subsidiaries FROM gold.dim_company") == [(["Acme Payments, LLC"],)]


def test_builds_with_empty_ref_tables_and_is_unknown(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    gold(db)
    assert company(db) == ("UNKNOWN", 0, 0, None)
    assert fct(db, "1")["sponsorship_class"] == "UNKNOWN"
    assert query(db, "SELECT count(*) FROM gold.agg_lca_company_year") == [(0,)]


def test_only_plain_certified_counts_as_evidence(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    load_ref(
        db,
        [
            lca_case(1),
            lca_case(2, positions=3),
            lca_case(3, status="CERTIFIED - WITHDRAWN"),
            lca_case(4, status="WITHDRAWN"),
            lca_case(5, status="DENIED"),
            lca_case(6, fy=2026),
        ],
    )
    gold(db)
    assert query(
        db,
        "SELECT fiscal_year, n_certified, n_certified_positions, n_certified_withdrawn, "
        "n_withdrawn, n_denied, n_cases FROM gold.agg_lca_company_year ORDER BY 1",
    ) == [(2025, 2, 4, 1, 1, 1, 5), (2026, 1, 1, 0, 0, 0, 1)]
    assert company(db) == ("HISTORICAL_EVIDENCE", 3, 3, 0.9)
    assert query(db, "SELECT lca_employer_names FROM gold.dim_company") == [(["Acme, Inc."],)]
    assert fct(db, "1")["sponsorship_class"] == "HISTORICAL_EVIDENCE"


def test_a_company_with_only_withdrawn_cases_has_no_evidence(
    storage: LocalStorage, db: Path
) -> None:
    seed(storage, db, [gh_job("1")])
    load_ref(db, [lca_case(1, status="WITHDRAWN"), lca_case(2, status="CERTIFIED - WITHDRAWN")])
    gold(db)
    assert company(db)[0] == "UNKNOWN"


def test_low_confidence_match_is_not_evidence(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    load_ref(db, [lca_case(1)], confidence=0.5)
    gold(db)
    assert company(db)[0] == "UNKNOWN"


def test_posting_statement_takes_precedence_over_lca_history(
    storage: LocalStorage, db: Path
) -> None:
    seed(storage, db, [gh_job("1"), gh_job("2", content="other"), gh_job("3", content="third")])
    add_extraction(
        db, "1", sponsorship_statement="not_offered",
        sponsorship_original_text="We do not sponsor visas.",
    )  # fmt: skip
    add_extraction(db, "2", sponsorship_statement="unspecified")
    load_ref(db, [lca_case(1)])
    gold(db)
    one = fct(db, "1")
    assert one["sponsorship_class"] == "EXPLICITLY_UNAVAILABLE"
    assert one["sponsorship_quote"] == "We do not sponsor visas."
    assert fct(db, "2")["sponsorship_class"] == "HISTORICAL_EVIDENCE"
    assert fct(db, "2")["sponsorship_quote"] is None
    # a posting statement never changes the company class; its counts sit beside it
    assert company(db)[0] == "HISTORICAL_EVIDENCE"
    assert query(db, "SELECT n_active_offered, n_active_not_offered FROM gold.dim_company") == [
        (0, 1)
    ]


def test_company_class_ignores_posting_statements_but_shows_counts(
    storage: LocalStorage, db: Path
) -> None:
    seed(storage, db, [gh_job("1"), gh_job("2", content="other")])
    add_extraction(
        db, "1", sponsorship_statement="offered",
        sponsorship_original_text="Visa sponsorship available",
    )  # fmt: skip
    add_extraction(
        db, "2", sponsorship_statement="not_offered", sponsorship_original_text="No sponsorship"
    )  # fmt: skip
    gold(db)
    assert company(db)[0] == "UNKNOWN"  # no filings: statements do not make a company class
    assert query(db, "SELECT n_active_offered, n_active_not_offered FROM gold.dim_company") == [
        (1, 1)
    ]  # mixed evidence stays visible


def test_role_family_wage_level_and_annualized_wage(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    load_ref(
        db,
        [
            lca_case(1, title="Senior Data Engineer", level="III"),
            lca_case(2, title="Data Scientist II", level="IV"),
            lca_case(3, title="Software Engineer", level=None),
            lca_case(4, title="Sales Data Scientist", level="N/A"),
            lca_case(5, title="Data Engineer", unit="HOUR", wage=50.0),
        ],
    )
    gold(db)
    assert query(
        db,
        "SELECT role_family, n_certified FROM gold.agg_lca_company_role ORDER BY role_family",
    ) == [("data_engineer", 2), ("data_scientist", 1), ("other", 2)]
    assert query(
        db, "SELECT wage_level, n_certified FROM gold.agg_lca_wage_level ORDER BY wage_level"
    ) == [("II", 1), ("III", 1), ("IV", 1), ("unknown", 2)]
    # hourly 50 x 2080 = 104,000 annual, not blended with advertised salary
    assert query(
        db,
        "SELECT max(wage_from_annual) FROM gold_stage.int_lca_certified "
        "WHERE case_number = 'I-200-00005'",
    ) == [(104000.0,)]
    # one row per (role family, state, city); percentiles stay NULL below the minimum count
    assert query(
        db,
        "SELECT sum(n_certified), sum(n_with_wage), count(p50_annual) "
        "FROM gold.agg_lca_wage_by_soc_city",
    ) == [(5, 5, 0)]


def test_implausible_wages_are_excluded_not_repaired(storage: LocalStorage, db: Path) -> None:
    seed(storage, db, [gh_job("1")])
    load_ref(
        db,
        [
            lca_case(1, wage=150000.0),
            lca_case(2, wage=142200.0, unit="HOUR"),  # annual amount labelled hourly: 295M
            lca_case(3, wage=10000.0),  # below the 15k floor
            lca_case(4, wage=50.0, unit="HOUR"),  # 104,000 annual: fine
        ],
    )
    gold(db)
    assert query(
        db,
        "SELECT case_number, wage_from_annual, wage_excluded FROM gold_stage.int_lca_certified "
        "ORDER BY 1",
    ) == [
        ("I-200-00001", 150000.0, False),
        ("I-200-00002", None, True),
        ("I-200-00003", None, True),
        ("I-200-00004", 104000.0, False),
    ]
    assert query(
        db,
        "SELECT sum(n_certified), sum(n_with_wage), sum(n_wage_excluded) "
        "FROM gold.agg_lca_wage_by_soc_city",
    ) == [(4, 2, 2)]
    assert query(db, "SELECT count(*) FROM gold.agg_lca_company_year WHERE n_certified = 4") == [
        (1,)
    ]  # the filings still count as evidence; only the wage is excluded
