"""Pure logic for the labeling app: sheet I/O, validation, and safe highlighting.

No Streamlit here, so it is unit-tested directly. This module only ever reads or writes the
owner's label sheets. It refuses any path that looks like a predictions file, so labeling stays
blind no matter what the UI is pointed at.
"""

from __future__ import annotations

import csv
import html
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import get_args

from jobpulse.extract.schema import RemotePolicy, RoleFamily, SalaryPeriod, Seniority

NOT_STATED = ""  # an empty enum cell means "the posting does not say"
SPONSOR_CHOOSE = ""  # targeted sheet: blank = not labeled yet


class BlindnessError(Exception):
    """Raised when asked to touch a predictions file."""


def _guard(path: Path) -> None:
    if "prediction" in path.name.lower():
        raise BlindnessError(f"refusing to open {path.name}: labeling must stay blind")


def _options(literal: object) -> list[str]:
    return [v for v in get_args(literal) if v != "unspecified"]


ENUM_OPTIONS: dict[str, list[str]] = {
    "seniority": _options(Seniority),
    "role_family": _options(RoleFamily),
    "remote_policy": _options(RemotePolicy),
    "salary_period": _options(SalaryPeriod),
    "sponsorship_statement": ["offered", "not_offered"],
}
# Order the fields appear in the form.
MAIN_ENUMS = ["seniority", "role_family", "remote_policy"]


@dataclass(frozen=True)
class SheetSpec:
    key: str
    title: str
    path: Path
    has_done: bool  # main sheet: done=y marks a row; targeted sheet: a filled statement does


def build_specs(labels: Path, sponsorship: Path) -> list[SheetSpec]:
    return [
        SheetSpec("main", "Main sample (all fields)", labels, True),
        SheetSpec("sponsorship", "Sponsorship sample (statement only)", sponsorship, False),
    ]


@dataclass
class Sheet:
    fieldnames: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)


def read_sheet(path: Path) -> Sheet:
    _guard(path)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        names = list(reader.fieldnames or [])
        return Sheet(names, [{k: (v or "") for k, v in r.items()} for r in reader])


def write_sheet(path: Path, sheet: Sheet) -> None:
    """Atomic write (temp file + rename) after keeping the previous version as <name>.bak."""
    _guard(path)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=sheet.fieldnames, lineterminator="\n")
            w.writeheader()
            w.writerows(sheet.rows)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def is_labeled(spec: SheetSpec, row: dict[str, str]) -> bool:
    if spec.has_done:
        return row.get("done", "").strip().lower() == "y"
    return row.get("sponsorship_statement", "").strip() != ""


def counts(spec: SheetSpec, rows: list[dict[str, str]], prefix: int = 100) -> tuple[int, int]:
    """(labeled overall, labeled within the first `prefix` rows)."""
    flags = [is_labeled(spec, r) for r in rows]
    return sum(flags), sum(flags[:prefix])


def next_unlabeled(spec: SheetSpec, rows: list[dict[str, str]], after: int) -> int | None:
    """Index of the next unlabeled row after `after`, wrapping around; None when all are done."""
    n = len(rows)
    for step in range(1, n + 1):
        i = (after + step) % n
        if not is_labeled(spec, rows[i]):
            return i
    return None


# --- validation -------------------------------------------------------------------------------
_NUM = re.compile(r"^\d+(?:\.\d+)?$")


def parse_money(text: str) -> tuple[str, str | None]:
    """'$120,000' -> '120000'; '120k' -> '120000'. Returns (normalized, error)."""
    raw = text.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not raw:
        return "", None
    mult = 1
    if raw[-1] in "kK":
        raw, mult = raw[:-1], 1000
    if not _NUM.match(raw):
        return "", f"{text.strip()!r} is not a number"
    value = float(raw) * mult
    return (str(int(value)) if value == int(value) else str(value)), None


def parse_skills(text: str) -> str:
    parts = [p.strip() for p in re.split(r"[;\n,|]", text) if p.strip()]
    seen: set[str] = set()
    out = []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return "; ".join(out)


def validate_main(values: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Clean the main-sheet form values. Returns (clean values, error messages)."""
    errors: list[str] = []
    clean = dict(values)
    lo, e1 = parse_money(values.get("salary_min", ""))
    hi, e2 = parse_money(values.get("salary_max", ""))
    errors += [f"salary_min: {e}" for e in [e1] if e] + [f"salary_max: {e}" for e in [e2] if e]
    clean["salary_min"], clean["salary_max"] = lo, hi
    cur = values.get("salary_currency", "").strip().upper()
    per = values.get("salary_period", "").strip()
    clean["salary_currency"], clean["salary_period"] = cur, per
    if lo and hi and float(lo) > float(hi):
        errors.append("salary_min is larger than salary_max")
    if lo or hi:
        if not re.fullmatch(r"[A-Z]{3}", cur):
            errors.append("salary_currency: use a 3-letter code such as USD")
        if not per:
            errors.append("salary_period: choose hour, day, week, month or year")
    elif cur or per:
        errors.append("salary_currency/salary_period are set but there is no salary amount")
    for name, options in ENUM_OPTIONS.items():
        v = clean.get(name, "")
        if v and v not in options:
            errors.append(f"{name}: {v!r} is not a valid choice")
    clean["skills"] = parse_skills(values.get("skills", ""))
    clean["notes"] = values.get("notes", "").strip()
    return clean, errors


SPONSOR_SHEET_VALUES = ("offered", "not_offered", "unspecified")


def validate_sponsorship(values: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    v = values.get("sponsorship_statement", "").strip()
    if v not in SPONSOR_SHEET_VALUES:
        return {}, ["choose offered, not_offered or unspecified"]
    return {"sponsorship_statement": v}, []


def save_row(spec: SheetSpec, posting_key: str, clean: dict[str, str]) -> None:
    """Re-read the sheet from disk, update only this row, and write it back atomically, so edits
    made elsewhere to other rows are never overwritten."""
    sheet = read_sheet(spec.path)
    for row in sheet.rows:
        if row["posting_key"] == posting_key:
            row.update({k: v for k, v in clean.items() if k in sheet.fieldnames})
            if spec.has_done:
                row["done"] = "y"
            write_sheet(spec.path, sheet)
            return
    raise KeyError(f"posting_key {posting_key} not found in {spec.path.name}")


# --- highlighting -----------------------------------------------------------------------------
_HL = re.compile(
    r"(?P<money>\$\s?\d+(?:,\d{3})*(?:\.\d+)?\s?[kKmM]?\b|\$"
    r"|\bsalary\b|\bcompensation\b|\bpay range\b)"
    r"|(?P<mode>\bremote\b|\bhybrid\b|\bon-?site\b)"
    r"|(?P<spons>\bsponsor\w*|\bvisa\w*|\bwork authori[sz]ation\b|\bh-?1b\b)",
    re.IGNORECASE,
)
_STYLE = {
    "money": "background:rgba(255,200,0,.45)",
    "mode": "background:rgba(80,160,255,.40)",
    "spons": "background:rgba(255,90,140,.45)",
}


def highlight_html(text: str) -> str:
    """HTML-escape the posting FIRST (untrusted text), then wrap keyword matches in <mark>."""
    escaped = html.escape(text)

    def _wrap(m: re.Match[str]) -> str:
        kind = m.lastgroup or "money"
        return f'<mark style="{_STYLE[kind]};color:inherit;padding:0 2px">{m.group(0)}</mark>'

    body = _HL.sub(_wrap, escaped)
    return (
        '<div style="white-space:pre-wrap;max-height:68vh;overflow-y:auto;padding:.75rem 1rem;'
        'border:1px solid rgba(128,128,128,.35);border-radius:6px;line-height:1.5">'
        f"{body}</div>"
    )
