"""Load DOL OFLC LCA disclosure files (H-1B) into the `ref` schema.

Reads the owner-downloaded .xlsx/.csv files, keeps only the columns named in config/lca.yaml and
only the configured visa classes, de-duplicates on CASE_NUMBER (the latest decision wins, so
overlapping quarterly files are harmless), then writes:

- ref.lca_employer_names   every distinct H-1B employer name per fiscal year with case counts (used
                           for match review; small, so names we do not match can still be reviewed)
- ref.employer_lca_match   which LCA names matched which company, by what method and confidence
- ref.lca_disclosures      the filings of matched employers only (we do not store all employers)

Evidence from public filings, not a promise of sponsorship.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

from jobpulse.config import LcaConfig, WarehouseConfig
from jobpulse.logging import get_logger
from jobpulse.ops.load_ops import transaction
from jobpulse.visa.match import Company, NameRow, build_matches
from jobpulse.visa.normalize import normalize_employer
from jobpulse.warehouse import bulk_insert

_FY = re.compile(r"FY[_ -]?(\d{4})", re.IGNORECASE)

_NAMES_DDL = """fiscal_year INTEGER NOT NULL, employer_name VARCHAR NOT NULL,
    employer_name_norm VARCHAR NOT NULL, n_cases INTEGER NOT NULL, n_certified INTEGER NOT NULL"""
_MATCH_DDL = """company_id VARCHAR NOT NULL, lca_employer_name VARCHAR NOT NULL,
    example_name VARCHAR NOT NULL, match_method VARCHAR NOT NULL, match_confidence DOUBLE NOT NULL,
    reviewed BOOLEAN NOT NULL, n_cases INTEGER NOT NULL, matched_at TIMESTAMPTZ NOT NULL"""
_DISCLOSURE_COLS = (
    "case_number", "fiscal_year", "source_file", "case_status", "received_date", "decision_date",
    "visa_class", "employer_name", "employer_name_norm", "company_id", "job_title", "soc_code",
    "soc_title", "full_time_position", "total_worker_positions", "worksite_city", "worksite_state",
    "wage_rate_from", "wage_rate_to", "wage_unit", "prevailing_wage", "pw_unit", "pw_wage_level",
)  # fmt: skip
_DISCLOSURES_DDL = """case_number VARCHAR NOT NULL, fiscal_year INTEGER NOT NULL,
    source_file VARCHAR NOT NULL, case_status VARCHAR, received_date DATE, decision_date DATE,
    visa_class VARCHAR, employer_name VARCHAR, employer_name_norm VARCHAR,
    company_id VARCHAR NOT NULL,
    job_title VARCHAR, soc_code VARCHAR, soc_title VARCHAR, full_time_position VARCHAR,
    total_worker_positions INTEGER, worksite_city VARCHAR, worksite_state VARCHAR,
    wage_rate_from DOUBLE, wage_rate_to DOUBLE, wage_unit VARCHAR, prevailing_wage DOUBLE,
    pw_unit VARCHAR, pw_wage_level VARCHAR, loaded_at TIMESTAMPTZ NOT NULL"""
_TEXT = (
    "case_status", "visa_class", "employer_name", "job_title", "soc_code", "soc_title",
    "full_time_position", "worksite_city", "worksite_state", "wage_unit", "pw_unit",
    "pw_wage_level",
)  # fmt: skip
_UPPER = ("case_status", "visa_class", "wage_unit", "pw_unit", "pw_wage_level", "worksite_state")
_DATES = ("received_date", "decision_date")
_NUMBERS = ("wage_rate_from", "wage_rate_to", "prevailing_wage")


class LcaLoadError(Exception):
    """The input files cannot be read as configured (missing columns, unreadable file, no files)."""


@dataclass
class LcaResult:
    files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    rows_read: int = 0
    rows_kept_visa_class: int = 0
    cases: int = 0
    distinct_names: int = 0
    companies_matched: int = 0
    match_rows: int = 0
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    disclosures_loaded: int = 0
    years: list[int] = field(default_factory=list)


def fiscal_year_of(path: Path) -> int | None:
    m = _FY.search(path.name)
    return int(m.group(1)) if m else None


def select_files(directory: Path, cfg: LcaConfig) -> tuple[list[tuple[Path, int]], list[str]]:
    chosen: list[tuple[Path, int]] = []
    skipped: list[str] = []
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() not in {".xlsx", ".csv"} or path.name.startswith("~"):
            continue
        fy = fiscal_year_of(path)
        if fy is None or fy not in cfg.fiscal_years:
            skipped.append(path.name)
        else:
            chosen.append((path, fy))
    return chosen, skipped


def ensure_ref_tables(con: duckdb.DuckDBPyConnection, cfg: WarehouseConfig) -> None:
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{cfg.ref_schema}"')
    s = cfg.ref_schema
    con.execute(f'CREATE TABLE IF NOT EXISTS "{s}"."lca_employer_names" ({_NAMES_DDL})')
    con.execute(f'CREATE TABLE IF NOT EXISTS "{s}"."employer_lca_match" ({_MATCH_DDL})')
    con.execute(f'CREATE TABLE IF NOT EXISTS "{s}"."lca_disclosures" ({_DISCLOSURES_DDL})')


def _quote_path(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _reader(path: Path) -> str:
    if path.suffix.lower() == ".xlsx":
        return f"read_xlsx({_quote_path(path)}, all_varchar = true)"
    return f"read_csv({_quote_path(path)}, all_varchar = true, header = true)"


def _staging_select(
    con: duckdb.DuckDBPyConnection, path: Path, fy: int, cfg: LcaConfig
) -> tuple[str, int]:
    """SQL that reads one file into our canonical columns, and the file's row count."""
    reader = _reader(path)
    try:
        header = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall()]
    except duckdb.Error as e:
        raise LcaLoadError(f"{path.name}: cannot read file ({type(e).__name__}: {e})") from None
    by_upper = {h.strip().upper(): h for h in header}
    actual: dict[str, str] = {}
    for canon, options in cfg.columns.items():
        for option in options:
            if option.upper() in by_upper:
                actual[canon] = by_upper[option.upper()]
                break
    missing = [c for c in cfg.required if c not in actual]
    if missing:
        raise LcaLoadError(
            f"{path.name}: missing required columns {missing}. "
            f"The file has: {', '.join(sorted(by_upper))[:1500]}"
        )

    def raw(canon: str) -> str:
        return f'"{actual[canon]}"' if canon in actual else "NULL"

    exprs: list[str] = []
    for c in ("case_number", *_TEXT):
        expr = f"nullif(trim({raw(c)}), '')"
        exprs.append(f"upper({expr}) AS {c}" if c in _UPPER else f"{expr} AS {c}")
    for c in _DATES:
        r = raw(c)
        exprs.append(
            f"coalesce(try_cast({r} AS DATE), CASE WHEN try_cast({r} AS DOUBLE) IS NOT NULL "
            f"THEN DATE '1899-12-30' + CAST(try_cast({r} AS DOUBLE) AS INTEGER) END) AS {c}"
        )
    for c in _NUMBERS:
        exprs.append(
            f"try_cast(nullif(regexp_replace({raw(c)}, '[^0-9.]', '', 'g'), '') AS DOUBLE) AS {c}"
        )
    exprs.append(
        f"try_cast(try_cast({raw('total_worker_positions')} AS DOUBLE) AS INTEGER) "
        "AS total_worker_positions"
    )
    exprs += [f"{fy} AS fiscal_year", f"'{path.name.replace(chr(39), '')}' AS source_file"]
    n = con.execute(f"SELECT count(*) FROM {reader}").fetchone()
    return f"SELECT {', '.join(exprs)} FROM {reader}", int(n[0]) if n else 0


def load_lca(
    target: duckdb.DuckDBPyConnection,
    wh: WarehouseConfig,
    cfg: LcaConfig,
    files: list[tuple[Path, int]],
    companies: list[Company],
    aliases: dict[str, list[str]],
    *,
    loaded_at: datetime,
    skipped: list[str] | None = None,
) -> LcaResult:
    if not files:
        raise LcaLoadError("no LCA files found for the configured fiscal years")
    log = get_logger(event_scope="lca")
    result = LcaResult(skipped_files=skipped or [])
    mem = duckdb.connect(":memory:")
    try:
        try:
            mem.execute("INSTALL excel")
            mem.execute("LOAD excel")
        except duckdb.Error as ext_error:
            if any(p.suffix.lower() == ".xlsx" for p, _ in files):
                raise LcaLoadError(f"cannot load DuckDB's excel extension: {ext_error}") from None
        first = True
        for path, fy in files:
            select, n_rows = _staging_select(mem, path, fy, cfg)
            verb = "CREATE TABLE stg AS" if first else "INSERT INTO stg"
            first = False
            classes = ", ".join("'" + c.upper().replace("'", "''") + "'" for c in cfg.visa_classes)
            mem.execute(f"{verb} SELECT * FROM ({select}) WHERE upper(visa_class) IN ({classes})")
            result.files.append(path.name)
            result.rows_read += n_rows
            log.info("lca_file_read", file=path.name, rows=n_rows)
        row = mem.execute("SELECT count(*) FROM stg").fetchone()
        result.rows_kept_visa_class = int(row[0]) if row else 0
        mem.execute(
            """CREATE TABLE cases AS SELECT * FROM stg WHERE case_number IS NOT NULL
               QUALIFY row_number() OVER (PARTITION BY case_number
                   ORDER BY decision_date DESC NULLS LAST, source_file DESC) = 1"""
        )
        row = mem.execute("SELECT count(*) FROM cases").fetchone()
        result.cases = int(row[0]) if row else 0

        raw_names = [r[0] for r in mem.execute(
            "SELECT DISTINCT employer_name FROM cases WHERE employer_name IS NOT NULL"
        ).fetchall()]  # fmt: skip
        suffixes = cfg.match.legal_suffixes
        mem.execute("CREATE TABLE norm_map (employer_name VARCHAR, employer_name_norm VARCHAR)")
        bulk_insert(
            mem,
            "norm_map",
            [(n, normalize_employer(n, suffixes)) for n in raw_names],
            chunk=2000,
        )
        certified = ", ".join(
            "'" + s.upper().replace("'", "''") + "'" for s in cfg.certified_statuses
        )
        names_rows = mem.execute(
            f"""SELECT c.fiscal_year, c.employer_name, m.employer_name_norm, count(*)::INTEGER,
                       count(*) FILTER (WHERE c.case_status IN ({certified}))::INTEGER
                FROM cases c JOIN norm_map m USING (employer_name)
                GROUP BY ALL"""
        ).fetchall()
        result.years = sorted({int(r[0]) for r in names_rows})
        result.distinct_names = len({r[2] for r in names_rows})

        s = wh.ref_schema
        ensure_ref_tables(target, wh)
        with transaction(target):
            years = ", ".join(str(y) for y in result.years)
            target.execute(f'DELETE FROM "{s}"."lca_employer_names" WHERE fiscal_year IN ({years})')
            target.execute(f'DELETE FROM "{s}"."lca_disclosures" WHERE fiscal_year IN ({years})')
            bulk_insert(target, f'"{s}"."lca_employer_names"', names_rows, chunk=1000)

            # Match against the names of ALL loaded years, not just this run's files.
            all_names = [
                NameRow(raw=r[0], norm=r[1], n_cases=int(r[2]))
                for r in target.execute(
                    f"""SELECT employer_name, employer_name_norm, sum(n_cases)::INTEGER
                        FROM "{s}"."lca_employer_names" GROUP BY ALL"""
                ).fetchall()
            ]
            mres = build_matches(all_names, companies, aliases, cfg.match)
            result.ambiguous = mres.ambiguous
            result.match_rows = len(mres.matches)
            result.companies_matched = len({m.company_id for m in mres.matches})
            target.execute(f'DELETE FROM "{s}"."employer_lca_match"')
            bulk_insert(
                target,
                f'"{s}"."employer_lca_match"',
                [
                    (
                        m.company_id,
                        m.norm_name,
                        m.example_name,
                        m.method,
                        m.confidence,
                        m.reviewed,
                        m.n_cases,
                        loaded_at,
                    )
                    for m in mres.matches
                ],
            )

            mem.execute("CREATE TABLE match_map (employer_name_norm VARCHAR, company_id VARCHAR)")
            bulk_insert(mem, "match_map", [(m.norm_name, m.company_id) for m in mres.matches])
            select_cols = ", ".join(
                {"employer_name_norm": "n.employer_name_norm", "company_id": "m.company_id"}.get(
                    c, f"c.{c}"
                )
                for c in _DISCLOSURE_COLS
            )
            rows = mem.execute(
                f"""SELECT {select_cols} FROM cases c JOIN norm_map n USING (employer_name)
                    JOIN match_map m ON m.employer_name_norm = n.employer_name_norm"""
            ).fetchall()
            ordered = [(*r, loaded_at) for r in rows]
            bulk_insert(target, f'"{s}"."lca_disclosures"', ordered, chunk=300)
            result.disclosures_loaded = len(ordered)
    finally:
        mem.close()
    return result
