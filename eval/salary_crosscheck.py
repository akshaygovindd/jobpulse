"""Rule-based salary cross-check: a regex pay-range parser vs the model's salary fields.

    uv run python eval/salary_crosscheck.py [--db local.duckdb]

NO human labels are involved, and neither side is ground truth. This measures AGREEMENT between a
conservative regex parser (eval/salary_rules.py) and the model, over every stored extraction whose
posting text is still available. Writes eval/reports/salary_crosscheck_report.md and a CSV of the
disagreements to spot-check by hand.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from auto_common import fetch_extractions, open_ctx, pct
from common import REPORTS_DIR
from salary_rules import PayRange, compare, parse_pay

ORDER = [
    "match",
    "match_envelope",
    "match_period_conflict",
    "partial",
    "mismatch",
    "model_missed",
    "model_only",
    "no_salary_both",
]
MEANING = {
    "match": "model amounts equal a parsed range",
    "match_envelope": "several ranges parsed; model gives the lowest low and highest high",
    "match_period_conflict": "amounts equal, explicit pay period differs",
    "partial": "one bound equals a parsed bound, the other differs",
    "mismatch": "parser found ranges; model's amounts match none",
    "model_missed": "parser found a range; model returned no salary",
    "model_only": "model returned a salary the parser could not find (unverifiable)",
    "no_salary_both": "neither found a salary",
}


def _fmt(r: PayRange) -> str:
    return f"{r.low:g}-{r.high:g} {r.period or r.inferred_period or '?'}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    ap.add_argument("--out-dir", default=str(REPORTS_DIR))
    args = ap.parse_args()
    ctx = open_ctx(args.db)
    if ctx is None:
        return 2
    tol = ctx.ecfg.eval.salary_tolerance
    rows = fetch_extractions(ctx)
    with_text = [r for r in rows if r["description_text"]]
    counts: Counter[str] = Counter()
    disagreements: list[list[str]] = []
    for r in with_text:
        ranges = parse_pay(r["description_text"])
        # an inferred period is not model output, so it is not used to judge the model
        model_period = None if r["salary_period_inferred"] else r["salary_period"]
        result = compare(ranges, r["salary_min"], r["salary_max"], model_period, tol)
        counts[result] += 1
        if result in {"match_period_conflict", "partial", "mismatch", "model_missed"}:
            disagreements.append(
                [
                    r["posting_key"],
                    r["title"],
                    r["company"],
                    result,
                    "; ".join(_fmt(x) for x in ranges[:4]),
                    f"{r['salary_min']}-{r['salary_max']} {r['salary_period'] or '?'}",
                    r["salary_original_text"] or "",
                ]
            )
    n = len(with_text)
    agree = counts["match"] + counts["match_envelope"] + counts["match_period_conflict"]
    both = agree + counts["partial"] + counts["mismatch"]
    parser_found = both + counts["model_missed"]
    model_found = both + counts["model_only"]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "salary_crosscheck_disagreements.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["posting_key", "title", "company", "result", "parsed", "model", "model_quote"])
        w.writerows(disagreements)
    lines = [
        "# Rule-based salary cross-check (agreement, not accuracy)",
        "",
        "A conservative regex parser (`eval/salary_rules.py`) and the model each read the same "
        "posting; this report counts how often they agree. **Neither side is ground truth, and "
        "no human labels are used.** The parser can be wrong (e.g. it may read a customer-savings "
        "range as pay) and can miss unusual formats, so a disagreement means 'look at this "
        "posting', not 'the model is wrong'. Produced by `eval/salary_crosscheck.py`.",
        "",
        f"- Extractions in the warehouse for the current prompt version: **{len(rows)}**; with "
        f"posting text still available (scored): **{n}**.",
        f"- Parser found at least one pay range in **{pct(parser_found, n)}**; the model returned "
        f"a salary for **{pct(model_found, n)}**.",
        f"- Where both found a salary, the model's amounts equal a parsed range in "
        f"**{pct(agree, both)}** of cases (of which overall-envelope answers: "
        f"{counts['match_envelope']}, period conflicts: {counts['match_period_conflict']}).",
        f"- Where the parser found a range, the model also returned a salary in "
        f"**{pct(both, parser_found)}** of cases (a low number would mean missed salaries).",
        "",
        "| Result | Postings | Share of scored | Meaning |",
        "|---|---|---|---|",
    ]
    for k in ORDER:
        lines.append(f"| {k} | {counts[k]} | {pct(counts[k], n)} | {MEANING[k]} |")
    lines += [
        "",
        "**Caveat on the headline rate:** the parser was refined once after a first look at its "
        "disagreements with the model (an 'overall envelope' class for multi-location postings and "
        "sanity bounds per pay period). The first, unrefined run agreed in 91.6% of cases where "
        "both found a salary. Whether an envelope answer is right is a judgment call, so it has "
        "its own row.",
        "",
        f"Salary amounts compare with a relative tolerance of {tol:g}. A posting that lists "
        "several ranges (for example by location) agrees if the model matches any of them. "
        f"Disagreements to spot-check: `{(out / 'salary_crosscheck_disagreements.csv').name}` "
        f"({len(disagreements)} rows).",
        "",
    ]
    (out / "salary_crosscheck_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
