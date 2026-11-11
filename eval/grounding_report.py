"""Validation and grounding pass rates, from the checks the pipeline already runs.

    uv run python eval/grounding_report.py [--db local.duckdb]

NO human labels are involved. A grounding pass means a quoted piece of evidence (the salary text,
the sponsorship sentence) really appears in the posting. It says the model did not invent the
quote; it does NOT say the extracted value is right. Writes eval/reports/grounding_report.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from auto_common import fetch_extractions, open_ctx, pct
from common import REPORTS_DIR

from jobpulse.extract.schema import quote_in_text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    ap.add_argument("--out-dir", default=str(REPORTS_DIR))
    args = ap.parse_args()
    ctx = open_ctx(args.db)
    if ctx is None:
        return 2
    s = ctx.ecfg.extract_schema
    rows = fetch_extractions(ctx)
    quar = ctx.con.execute(
        f'SELECT count(*) FROM "{s}"."quarantine" WHERE schema_version = ? AND prompt_version = ?',
        [ctx.ecfg.schema_version, ctx.prompt_version],
    ).fetchone()
    n_quar = int(quar[0]) if quar else 0
    first_id = ctx.ecfg.first_pass.id
    call_rows = ctx.con.execute(
        f'SELECT outcome, count(*) FROM "{s}"."llm_calls" WHERE model = ? AND mode <> \'eval\' '
        "GROUP BY 1",
        [first_id],
    ).fetchall()
    calls = {str(o): int(c) for o, c in call_rows}
    first_ok, first_bad = calls.get("ok", 0), calls.get("escalated", 0)

    def check(field: str) -> tuple[int, int, int]:
        have = [r for r in rows if r[field]]
        scored = [r for r in have if r["description_text"]]
        ok = sum(quote_in_text(r[field], r["description_text"]) for r in scored)
        return len(have), len(scored), ok

    sal_have, sal_scored, sal_ok = check("salary_original_text")
    sp_have, sp_scored, sp_ok = check("sponsorship_original_text")
    n = len(rows)
    n_escalated = sum(1 for r in rows if r["escalated"])
    lines = [
        "# Validation and grounding pass rates (self-consistency, not accuracy)",
        "",
        "These come from checks the pipeline already enforces when it accepts an extraction "
        "(structure, salary/sponsorship consistency, and 'the quoted text appears in the "
        "posting'). **No human labels are used and none of this measures whether a value is "
        "correct.** Produced by `eval/grounding_report.py`.",
        "",
        "## Did the model's output pass validation?",
        "",
        f"- First-pass model (`{first_id}`) calls that passed validation without escalation: "
        f"**{pct(first_ok, first_ok + first_bad)}**. Failures here include both structural and "
        "grounding failures; the pipeline does not record which, so they cannot be split.",
        f"- Postings accepted after validation: **{pct(n, n + n_quar)}**; quarantined (failed both "
        f"models): **{n_quar}**.",
        f"- Accepted from the escalation model: **{pct(n_escalated, n)}**.",
        "",
        "## Re-check of stored quotes against the posting text",
        "",
        "| Quote field | Extractions with a quote | Text available | Quote found in text |",
        "|---|---|---|---|",
        f"| salary_original_text | {sal_have} | {sal_scored} | {pct(sal_ok, sal_scored)} |",
        f"| sponsorship_original_text | {sp_have} | {sp_scored} | {pct(sp_ok, sp_scored)} |",
        "",
        "Every accepted extraction passed this check when it was stored, so anything below 100% "
        "here means the posting text changed afterwards or the stored data was altered. 'Text "
        "available' excludes postings that have since left the active data.",
        "",
    ]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "grounding_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
