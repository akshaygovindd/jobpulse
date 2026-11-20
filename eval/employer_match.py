"""Employer-match review: build the blind pair sheet and score its yes/no labels.

The labels were drafted with AI assistance and reviewed by the owner.

Pairs are (company, LCA employer name). They come from two places, mixed and shuffled so the
labeler cannot tell which is which:
- matched: names our deterministic rules matched (these are what the visa evidence relies on);
- suggested: near-misses found by similarity, which are NOT matches and never count as evidence.
Precision is reported for the matched pairs only; suggested pairs the owner labels "yes" are
candidates for config/lca_aliases.yaml (missed matches).
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from jobpulse.config import LcaConfig, WarehouseConfig
from jobpulse.visa.match import NameRow, Suggestion, build_companies, suggest

SHEET_COLUMNS = ["pair_id", "company", "lca_employer_name", "n_cases_all_years", "label"]
VALID_LABELS = {"yes", "no", "unsure"}


@dataclass(frozen=True)
class Pair:
    pair_id: str
    company_id: str
    company: str
    norm_name: str
    display_name: str
    n_cases: int
    origin: str  # "matched" or "suggested"
    method: str | None
    confidence: float | None
    similarity: float | None


def _name_rows(con: duckdb.DuckDBPyConnection, ref: str) -> list[NameRow]:
    return [
        NameRow(raw=r[0], norm=r[1], n_cases=int(r[2]))
        for r in con.execute(
            f"SELECT employer_name, employer_name_norm, sum(n_cases)::INTEGER "
            f'FROM "{ref}"."lca_employer_names" GROUP BY ALL'
        ).fetchall()
    ]


def build_pairs(
    con: duckdb.DuckDBPyConnection,
    wh: WarehouseConfig,
    lca: LcaConfig,
    company_names: list[str],
    *,
    size: int | None = None,
) -> list[Pair]:
    """Pick the pairs to review: matched ones first (up to 60% of the sheet), then suggestions
    spread across companies, shuffled with the configured seed."""
    size = size or lca.review.sample_size
    ref = wh.ref_schema
    companies = build_companies(company_names, lca.match)
    name_of = {c.company_id: c.name for c in companies}
    matched = con.execute(
        "SELECT company_id, lca_employer_name, example_name, match_method, "
        "match_confidence, n_cases "
        f'FROM "{ref}"."employer_lca_match"'
    ).fetchall()
    rng = random.Random(lca.review.seed)
    rng.shuffle(matched)
    take_matched = matched[: max(1, int(size * 0.6))]
    pairs: list[Pair] = [
        Pair("", cid, name_of[cid], norm, ex, int(n), "matched", method, float(conf), None)
        for cid, norm, ex, method, conf, n in take_matched
    ]
    matched_norms = {m[1] for m in matched}
    suggestions = suggest(_name_rows(con, ref), companies, matched_norms, lca.match)
    by_company: dict[str, list[Suggestion]] = defaultdict(list)
    for s in suggestions:
        by_company[s.company_id].append(s)
    for lst in by_company.values():
        lst.sort(key=lambda s: (-s.similarity, -s.n_cases))
    # round-robin over companies so one company with many near-misses does not fill the sheet
    order = sorted(by_company)
    i = 0
    while len(pairs) < size and any(by_company.values()):
        cid = order[i % len(order)]
        i += 1
        if by_company[cid]:
            s = by_company[cid].pop(0)
            pairs.append(
                Pair(
                    "",
                    cid,
                    name_of[cid],
                    s.norm_name,
                    s.example_name,
                    s.n_cases,
                    "suggested",
                    None,
                    None,
                    s.similarity,
                )
            )
    rng.shuffle(pairs)
    return [
        Pair(
            f"P{i + 1:03d}",
            p.company_id,
            p.company,
            p.norm_name,
            p.display_name,
            p.n_cases,
            p.origin,
            p.method,
            p.confidence,
            p.similarity,
        )
        for i, p in enumerate(pairs)
    ]


def write_sheet(pairs: list[Pair], sheet: Path, manifest: Path, seed: int) -> None:
    with sheet.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SHEET_COLUMNS)
        for p in pairs:
            w.writerow([p.pair_id, p.company, p.display_name, p.n_cases, ""])
    manifest.write_text(
        json.dumps(
            {
                "seed": seed,
                "note": "Not shown to the labeler: origin, method, confidence, similarity.",
                "pairs": [
                    {k: v for k, v in p.__dict__.items() if k not in {"company", "display_name"}}
                    for p in pairs
                ],
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def read_labels(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            label = (row.get("label") or "").strip().lower()
            if not label:
                continue
            if label not in VALID_LABELS:
                raise ValueError(
                    f"{row['pair_id']}: label must be yes, no or unsure, got {label!r}"
                )
            out[row["pair_id"]] = label
    return out


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def score(labels: dict[str, str], manifest: dict[str, Any], target: int = 50) -> dict[str, Any]:
    pairs = {p["pair_id"]: p for p in manifest["pairs"]}
    unknown = sorted(set(labels) - set(pairs))
    if unknown:
        raise ValueError(f"labels for unknown pairs: {unknown}")
    matched = [(pairs[i], v) for i, v in labels.items() if pairs[i]["origin"] == "matched"]
    suggested = [(pairs[i], v) for i, v in labels.items() if pairs[i]["origin"] == "suggested"]

    def prec(rows: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
        yes = sum(1 for _, v in rows if v == "yes")
        no = sum(1 for _, v in rows if v == "no")
        unsure = sum(1 for _, v in rows if v == "unsure")
        lo, hi = wilson(yes, yes + no)
        return {
            "yes": yes,
            "no": no,
            "unsure": unsure,
            "n": yes + no,
            "precision": yes / (yes + no) if yes + no else None,
            "ci95": (lo, hi),
        }

    by_method: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for p, v in matched:
        by_method[p["method"]].append((p, v))
    return {
        "labeled_pairs": len(labels),
        "total_pairs": len(pairs),
        "target": target,
        "meets_target": len(labels) >= target,
        "matched": prec(matched),
        "by_method": {m: prec(rows) for m, rows in sorted(by_method.items())},
        "suggested_yes": [p for p, v in suggested if v == "yes"],
        "suggested_no": sum(1 for _, v in suggested if v == "no"),
        "matched_wrong": [p for p, v in matched if v == "no"],
    }


def render(result: dict[str, Any]) -> str:
    m = result["matched"]

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{100 * x:.1f}%"

    lines = [
        "# Employer-match precision",
        "",
        "Evidence from public filings, not a promise of sponsorship. Labels were drafted with AI "
        "assistance and reviewed by the owner.",
        "",
        f"- Labeled pairs: **{result['labeled_pairs']}** of {result['total_pairs']} "
        f"(target: at least {result['target']}: {'met' if result['meets_target'] else 'NOT met'})",
        f"- Matched pairs labeled yes/no: **{m['n']}** (unsure: {m['unsure']})",
        f"- **Precision of our matches: {pct(m['precision'])}** "
        f"({m['yes']} yes, {m['no']} no); 95% interval {pct(m['ci95'][0])} to {pct(m['ci95'][1])} "
        "(Wilson). With few pairs the interval is wide: read it, not just the point value.",
        "",
        "| Method | yes | no | unsure | precision |",
        "|---|---|---|---|---|",
    ]
    for method, r in result["by_method"].items():
        lines.append(
            f"| {method} | {r['yes']} | {r['no']} | {r['unsure']} | {pct(r['precision'])} |"
        )
    lines += [
        "",
        "Scope: the sample is the match set at the time the sheet was drawn. Aliases, subsidiaries "
        "and exclusions added afterwards (for example the Mercury exclusion) are not in it.",
    ]
    lines += ["", "## Wrong matches (fix the rule or remove the alias)"]
    wrong = result["matched_wrong"]
    lines += [
        f"- {p['pair_id']}: {p['company_id']} <- `{p['norm_name']}` ({p['method']})" for p in wrong
    ] or ["- none"]
    lines += [
        "",
        "## Missed matches: suggested pairs labeled yes (candidates for config/lca_aliases.yaml)",
    ]
    missed = result["suggested_yes"]
    lines += [f"- {p['company_id']}: `{p['norm_name']}`" for p in missed] or ["- none"]
    lines.append(
        f"\nSuggested pairs labeled no: {result['suggested_no']} (similar names, not the same "
        "employer: why similarity is never a match)."
    )
    return "\n".join(lines) + "\n"
