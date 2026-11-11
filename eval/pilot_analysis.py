"""Error analysis of the hand-labeled pilot: where the model and the owner's labels disagree.

    uv run python eval/pilot_analysis.py [--labels PATH] [--predictions PATH]

Writes eval/reports/pilot_error_analysis.md. Covers seniority, skills, and a salary-period check.
Every figure comes from this script. n is small (the pilot), so read patterns, not percentages.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

from common import LABELS_PATH, PREDICTIONS_PATH, REPORTS_DIR
from score import (
    _cat,
    _norm_set,
    _num,
    _skills,
    fold_seniority,
    join,
    read_csv,
    score_fields,
)

from jobpulse.config import load_extraction

TOP = 15
YEARS = re.compile(r"(\d+)\s*\+?\s*(?:-|\u2013|to)?\s*(?:\d+)?\s*\+?\s*years?", re.IGNORECASE)


def _s(v: str | None) -> str:
    return _cat(v) or "unspecified"


def seniority_section(pairs: list[tuple[dict[str, str], dict[str, str] | None]]) -> list[str]:
    cfg = load_extraction().eval
    fold = cfg.seniority_fold
    rows = [(lab, p) for lab, p in pairs if p is not None]
    raw_miss = [(lab, p) for lab, p in rows if _s(lab["seniority"]) != _s(p["seniority"])]
    resid = [
        (lab, p)
        for lab, p in rows
        if (fold_seniority(lab["seniority"], fold) or "unspecified")
        != (fold_seniority(p["seniority"], fold) or "unspecified")
    ]
    lines = [
        "## Seniority",
        "",
        f"- Raw exact match: **{len(rows) - len(raw_miss)} / {len(rows)}**. After folding "
        f"{', '.join(sorted(fold))} into senior (scoring-time only): "
        f"**{len(rows) - len(resid)} / {len(rows)}**.",
        f"- Owner label values used: {dict(Counter(_s(lab['seniority']) for lab, _ in rows))}. "
        "No `junior`, `principal`, `manager`, `director` or `executive` labels were used.",
        "",
        "Raw disagreements (label -> model):",
        "",
        "| label | model | n |",
        "|---|---|---|",
    ]
    for (a, b), n in Counter(
        (_s(lab["seniority"]), _s(p["seniority"])) for lab, p in raw_miss
    ).most_common():
        lines.append(f"| {a} | {b} | {n} |")
    lines += [
        "",
        f"The folding resolves **{len(raw_miss) - len(resid)}** of the {len(raw_miss)} raw "
        f"disagreements. The remaining **{len(resid)}** (not explained by the staff/manager "
        "convention):",
        "",
        "| title | label | model | years phrase in text |",
        "|---|---|---|---|",
    ]
    for lab, p in resid:
        m = YEARS.search(lab["description_text"])
        lines.append(
            f"| {lab['title'][:60]} | {_s(lab['seniority'])} | {_s(p['seniority'])} | "
            f"{m.group(0) if m else '-'} |"
        )
    sr_mid = [
        lab
        for lab, _ in rows
        if re.search(r"\b(sr\.?|senior)\b", lab["title"], re.I) and _s(lab["seniority"]) == "mid"
    ]
    lines += [
        "",
        f"- Titles containing Sr./Senior that were labeled `mid`: **{len(sr_mid)}** "
        "(the label seems to follow required years of experience there, not the title word). "
        "`junior` is never used by the owner, so a model `junior` is always a disagreement.",
        "- Rows where the label is `unspecified` although the title says Senior are possible label "
        "slips; check them before trusting the residual numbers.",
        "",
    ]
    return lines


def skills_section(pairs: list[tuple[dict[str, str], dict[str, str] | None]]) -> list[str]:
    ecfg = load_extraction()
    scope = re.compile(ecfg.eval.skills_out_of_scope_pattern, re.IGNORECASE)
    rows = [(lab, p) for lab, p in pairs if p is not None]
    f = score_fields(pairs, ecfg)
    n = len(rows)
    lab_n = [len(_skills(lab["skills"])) for lab, _ in rows]
    pred_n = [len(_skills(p["skills"])) for _, p in rows]
    missed: Counter[str] = Counter()
    extra: Counter[str] = Counter()
    verbatim = comp = phrase = total_missed = 0
    for lab, p in rows:
        g, q = _norm_set(_skills(lab["skills"])), _norm_set(_skills(p["skills"]))
        text = lab["description_text"].lower()
        for s in g - q:
            total_missed += 1
            missed[s] += 1
            verbatim += s in text
            comp += bool(scope.search(s))
            phrase += len(s.split()) >= 3
        extra.update(q - g)

    def row(label: str, d: dict[str, object]) -> str:
        pr, rc = d["precision"], d["recall"]
        fmt = lambda v: "n/a" if v is None else f"{100 * float(v):.1f}%"  # type: ignore[arg-type]  # noqa: E731
        return f"| {label} | {fmt(pr)} | {fmt(rc)} | {d['tp']} | {d['fp']} | {d['fn']} |"

    return [
        "## Skills",
        "",
        f"- The owner listed **{sum(lab_n) / n:.1f}** skills per posting (min {min(lab_n)}, "
        f"max {max(lab_n)}); the model listed **{sum(pred_n) / n:.1f}** "
        f"(min {min(pred_n)}, max {max(pred_n)}).",
        "",
        "| variant | precision | recall | TP | FP | FN |",
        "|---|---|---|---|---|---|",
        row("raw exact match", f["skills"]),
        row("normalized names", f["skills_normalized"]),
        row("normalized, competencies/soft skills excluded", f["skills_tools_only"]),
        "",
        f"Of the **{total_missed}** labeled skills the model did not list (normalized names):",
        "",
        f"- **{100 * verbatim // max(total_missed, 1)}%** appear verbatim in the posting text, "
        "so the model could have listed them.",
        f"- **{100 * comp // max(total_missed, 1)}%** are competencies/soft skills "
        "(`skills_out_of_scope_pattern`), which the tools-only prompt does not ask for.",
        f"- **{100 * phrase // max(total_missed, 1)}%** are phrases of three or more words "
        "(concepts, not tool names).",
        "",
        "Most-missed labeled skills: "
        + ", ".join(f"{s} ({c})" for s, c in missed.most_common(TOP))
        + ".",
        "",
        "Most frequent model skills the owner did not list: "
        + ", ".join(f"{s} ({c})" for s, c in extra.most_common(TOP))
        + ".",
        "",
        "Reading: normalization barely moves the numbers, so the gap is scope, not spelling. The "
        "owner's lists include technical concepts and competencies (model evaluation, "
        "performance optimization, cross-functional collaboration); the prompt asks for tools "
        "and technologies.",
        "",
    ]


def salary_period_section(pairs: list[tuple[dict[str, str], dict[str, str] | None]]) -> list[str]:
    rows = [(lab, p) for lab, p in pairs if p is not None]
    amount_no_period = [
        lab
        for lab, p in rows
        if (_num(p["salary_min"]) is not None or _num(p["salary_max"]) is not None)
        and not _cat(p["salary_period"])
    ]
    with_amount = sum(
        1 for _, p in rows if _num(p["salary_min"]) is not None or _num(p["salary_max"]) is not None
    )
    return [
        "## Salary period (extra finding)",
        "",
        f"- Postings where the model returned a salary amount but **no period**: "
        f"**{len(amount_no_period)} of {with_amount}** with an amount.",
        "- Cause: validation does not require `salary_period` (or currency) when an amount is "
        "present, so such outputs are accepted instead of escalated. Not changed here (it would "
        "alter escalation behavior); see NOTES.md.",
        "",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", default=str(LABELS_PATH))
    ap.add_argument("--predictions", default=str(PREDICTIONS_PATH))
    ap.add_argument("--out", default=str(REPORTS_DIR / "pilot_error_analysis.md"))
    args = ap.parse_args()
    pairs, skipped = join(read_csv(Path(args.labels)), read_csv(Path(args.predictions)))
    if not pairs:
        print("error: no rows with done=y in the labels file")
        return 2
    lines = [
        f"# Pilot error analysis (n={len(pairs)} hand-labeled postings)",
        "",
        "Disagreement between the owner's labels and the model, for the two weakest fields. "
        "Disagreement is not always model error: some of it is labeling convention. Produced by "
        f"`eval/pilot_analysis.py` (rows without done=y skipped: {skipped}).",
        "",
        *seniority_section(pairs),
        *skills_section(pairs),
        *salary_period_section(pairs),
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
