"""Cross-model AGREEMENT: run the escalation model on a seeded sample and compare field by field
with the first-pass model's stored output.

    uv run python eval/agreement.py --dry-run     # sample + cost estimate, no API call
    uv run python eval/agreement.py --run         # call the escalation model (paid, capped)
    uv run python eval/agreement.py --report      # (re)write the report from stored results

NO human labels are involved. Two models agreeing does not make either one correct: this measures
CONSISTENCY between gpt-6-luna and gpt-6.1-terra on the same prompt, not accuracy. Calls are logged
in llm_calls with mode='eval' (excluded from the extraction cost figures) and results are stored in
extract.agreement; the extraction cache is never touched. Re-running only pays for postings not yet
done, and the cap (config eval.agreement.cap_usd) counts what earlier runs already spent.
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_common import Ctx, open_ctx, pct
from common import REPORTS_DIR
from score import fold_seniority, norm_skill

from jobpulse.extract.budget import Budget
from jobpulse.extract.cost import call_cost, estimate_input_tokens, worst_case_cost
from jobpulse.extract.llm import LLMClient, LLMError, LLMRequest, LLMResponse, OpenAIClient
from jobpulse.extract.schema import (
    load_prompt,
    parse_and_check,
    strict_json_schema,
    user_message,
)
from jobpulse.extract.store import CallRecord, Posting, Store
from jobpulse.http import HttpClient
from jobpulse.secrets import load_env_keys

CATEGORICAL = ["seniority", "role_family", "remote_policy", "salary_currency", "salary_period"]
NUMERIC = ["salary_min", "salary_max"]
FIELDS = [*CATEGORICAL, *NUMERIC, "sponsorship_statement"]


def sample_postings(ctx: Ctx, store: Store) -> list[Posting]:
    """Seeded sample of postings whose stored extraction came from the FIRST-PASS model (an
    escalated row would compare the escalation model with itself)."""
    s, silver = ctx.ecfg.extract_schema, ctx.pipeline.warehouse.silver_schema
    hashes = [
        str(r[0])
        for r in ctx.con.execute(
            f"""SELECT DISTINCT e.content_hash
                FROM "{s}"."extractions" e
                JOIN "{silver}"."int_extraction_candidates" c USING (content_hash)
                WHERE e.schema_version = ? AND e.prompt_version = ? AND e.model = ?
                ORDER BY e.content_hash""",
            [ctx.ecfg.schema_version, ctx.prompt_version, ctx.ecfg.first_pass.id],
        ).fetchall()
    ]
    cfg = ctx.ecfg.eval.agreement
    chosen = random.Random(cfg.seed).sample(hashes, min(cfg.size, len(hashes)))
    by_hash = store.posting_by_hash(chosen)
    return [by_hash[h] for h in sorted(chosen) if h in by_hash]


def estimate(ctx: Ctx, postings: list[Posting], done: set[str]) -> dict[str, Any]:
    cfg, cm = ctx.ecfg, ctx.ecfg.cost_model
    model = cfg.escalation
    todo = [p for p in postings if p.content_hash not in done]
    expected = sum(
        call_cost(
            model,
            estimate_input_tokens(
                len(p.title) + min(len(p.description_text), cfg.max_description_chars), cm
            ),
            cm.expected_output_tokens,
        )
        for p in todo
    )
    obs = ctx.con.execute(
        f"SELECT count(*), avg(cost_usd), avg(input_tokens), avg(output_tokens) "
        f'FROM "{cfg.extract_schema}"."llm_calls" '
        "WHERE model = ? AND mode <> 'eval' AND outcome = 'ok'",
        [model.id],
    ).fetchone()
    n_obs, avg_cost, avg_in, avg_out = obs or (0, None, None, None)
    worst = max(
        (worst_case_cost(model, len(p.title) + len(p.description_text), cfg) for p in todo),
        default=0.0,
    )
    return {
        "n_total": len(postings),
        "n_todo": len(todo),
        "expected_usd": expected,
        "observed_n": int(n_obs or 0),
        "observed_avg_call_usd": float(avg_cost) if avg_cost else None,
        "observed_avg_tokens": (float(avg_in), float(avg_out)) if avg_in else None,
        "observed_based_usd": len(todo) * float(avg_cost) if avg_cost else None,
        "worst_case_per_call_usd": worst,
        "prior_spend_usd": ctx.con.execute(
            f'SELECT coalesce(sum(cost_usd),0) FROM "{cfg.extract_schema}"."llm_calls" '
            "WHERE mode = 'eval' AND model = ?",
            [model.id],
        ).fetchone()[0],  # type: ignore[index]
    }


def print_estimate(ctx: Ctx, est: dict[str, Any]) -> None:
    cfg, model = ctx.ecfg.eval.agreement, ctx.ecfg.escalation
    print(
        f"escalation model: {model.id} (${model.input_usd_per_mtok}/M in, "
        f"${model.output_usd_per_mtok}/M out), prompt version {ctx.prompt_version}"
    )
    print(f"sample: {est['n_total']} postings (seed {cfg.seed}); still to run: {est['n_todo']}")
    print(f"expected cost (config token model): ${est['expected_usd']:.3f}")
    if est["observed_based_usd"] is not None:
        ai, ao = est["observed_avg_tokens"]
        print(
            f"expected cost (observed {model.id} calls, n={est['observed_n']}, avg {ai:.0f} in / "
            f"{ao:.0f} out tokens, ${est['observed_avg_call_usd']:.4f}/call): "
            f"${est['observed_based_usd']:.3f}"
        )
    print(
        f"spend cap: ${cfg.cap_usd:.2f}; already spent on eval calls: ${est['prior_spend_usd']:.4f}"
    )
    print(
        "worst case per call (max output tokens x safety factor): "
        f"${est['worst_case_per_call_usd']:.4f}; calls run one at a time ({cfg.workers} worker)"
    )


def run_agreement(
    store: Store, ctx: Ctx, client: LLMClient, postings: list[Posting], clock: Any = None
) -> dict[str, int | float]:
    cfg = ctx.ecfg
    model = cfg.escalation
    clock = clock or (lambda: datetime.now(UTC))
    done = store.agreement_done(model.id)
    budget = Budget(cfg.eval.agreement.cap_usd)
    budget.add_spent(store.eval_spend(model.id))
    system, schema = load_prompt(), strict_json_schema()
    attempt_id = f"eval-{uuid.uuid4().hex[:12]}"
    stats: dict[str, int | float] = {
        "ok": 0,
        "invalid": 0,
        "error": 0,
        "not_run_cap": 0,
        "skipped_done": 0,
        "cost_usd": 0.0,
    }
    for p in postings:
        if p.content_hash in done:
            stats["skipped_done"] += 1
            continue
        text = p.description_text[: cfg.max_description_chars]
        est = worst_case_cost(model, len(p.title) + len(text), cfg)
        if not budget.try_reserve(est):
            stats["not_run_cap"] += 1
            continue
        req = LLMRequest(
            custom_id=p.content_hash,
            model=model,
            system=system,
            user=user_message(p.title, p.company, p.location_raw, text),
            schema=schema,
            max_output_tokens=cfg.cost_model.max_output_tokens,
        )
        resp: LLMResponse | LLMError
        try:
            resp = client.complete(req)
        except LLMError as e:
            resp = e
        except Exception as e:  # isolation: one failing call never stops the run
            resp = LLMError("unexpected_error", type(e).__name__)
        now = clock()
        if isinstance(resp, LLMError):
            outcome, cost, tin, tout, cached, lat = "error", 0.0, 0, 0, 0, 0
            ex = None
        else:
            cost = call_cost(model, resp.input_tokens, resp.output_tokens, resp.cached_tokens)
            tin, tout, cached, lat = (
                resp.input_tokens,
                resp.output_tokens,
                resp.cached_tokens,
                resp.latency_ms,
            )
            ex = parse_and_check(resp.text, text)[0] if resp.text is not None else None
            outcome = "ok" if ex is not None else "invalid"
        budget.settle(est, cost)
        store.save_calls(
            [
                CallRecord(
                    uuid.uuid4().hex,
                    p.content_hash,
                    model.id,
                    "eval",
                    tin,
                    tout,
                    cached,
                    cost,
                    lat,
                    outcome,
                )
            ],
            attempt_id,
            now,
        )
        stats[outcome] += 1
        stats["cost_usd"] = float(stats["cost_usd"]) + cost
        if ex is not None:
            store.save_agreement(p.content_hash, model.id, ex, now)
    return stats


# --- comparison -------------------------------------------------------------------------------
def _same(a: Any, b: Any, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float | int) and isinstance(b, float | int):
        return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)
    return bool(a == b)


def compare_fields(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]], fold: dict[str, str], tol: float
) -> dict[str, dict[str, int]]:
    """Per field: n compared, n agreeing (null == null counts), and the same restricted to postings
    where at least one model returned a value."""
    out: dict[str, dict[str, int]] = {}
    for f in [*FIELDS, "seniority_folded"]:
        n = agree = either = agree_either = 0
        for a, b in pairs:
            if f == "seniority_folded":
                va, vb = fold_seniority(a["seniority"], fold), fold_seniority(b["seniority"], fold)
            else:
                va, vb = a[f], b[f]
                if f in CATEGORICAL or f == "sponsorship_statement":
                    va = None if va in (None, "", "unspecified") else va
                    vb = None if vb in (None, "", "unspecified") else vb
            same = _same(va, vb, tol)
            n += 1
            agree += same
            if va is not None or vb is not None:
                either += 1
                agree_either += same
        out[f] = {"n": n, "agree": agree, "either": either, "agree_either": agree_either}
    return out


def compare_skills(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, float]:
    jac: list[float] = []
    a_in_b = a_tot = b_in_a = b_tot = 0
    for a, b in pairs:
        sa = {norm_skill(s) for s in (a["skills"] or []) if norm_skill(s)}
        sb = {norm_skill(s) for s in (b["skills"] or []) if norm_skill(s)}
        if sa or sb:
            jac.append(len(sa & sb) / len(sa | sb))
        a_in_b, a_tot, b_in_a, b_tot = (
            a_in_b + len(sa & sb),
            a_tot + len(sa),
            b_in_a + len(sa & sb),
            b_tot + len(sb),
        )
    return {
        "mean_jaccard": sum(jac) / len(jac) if jac else 0.0,
        "first_in_second": a_in_b / a_tot if a_tot else 0.0,
        "second_in_first": b_in_a / b_tot if b_tot else 0.0,
        "n": len(jac),
    }


def write_report(ctx: Ctx, store: Store, stats: dict[str, Any] | None, out_dir: Path) -> str:
    cfg = ctx.ecfg
    s = cfg.extract_schema
    cols = [
        "content_hash",
        *[f for f in [*CATEGORICAL, "sponsorship_statement", "skills", *NUMERIC]],
    ]
    sel_a = ", ".join(f"e.{c}" for c in cols[1:])
    sel_b = ", ".join(f"g.{c}" for c in cols[1:])
    rows = ctx.con.execute(
        f"""SELECT e.content_hash, {sel_a}, {sel_b}
            FROM "{s}"."extractions" e
            JOIN "{s}"."agreement" g USING (content_hash, schema_version, prompt_version)
            WHERE e.schema_version = ? AND e.prompt_version = ? AND g.model = ? AND e.model = ?""",
        [cfg.schema_version, ctx.prompt_version, cfg.escalation.id, cfg.first_pass.id],
    ).fetchall()
    k = len(cols) - 1
    pairs = [
        (
            dict(zip(cols[1:], r[1 : 1 + k], strict=True)),
            dict(zip(cols[1:], r[1 + k : 1 + 2 * k], strict=True)),
        )
        for r in rows
    ]
    fields = compare_fields(pairs, cfg.eval.seniority_fold, cfg.eval.salary_tolerance)
    sk = compare_skills(pairs)
    spent = store.eval_spend(cfg.escalation.id)
    lines = [
        "# Cross-model agreement (consistency, NOT accuracy)",
        "",
        f"`{cfg.first_pass.id}` (stored first-pass output) vs `{cfg.escalation.id}` (re-run now), "
        "same prompt, same schema, on a seeded random sample. **Agreement means two models gave "
        "the same answer. It does not mean the answer is right: both can be wrong in the same "
        "way, and a disagreement does not say which model is wrong.** No human labels are used. "
        "Produced by `eval/agreement.py`.",
        "",
        f"- Postings compared: **{len(pairs)}** (sample size {cfg.eval.agreement.size}, seed "
        f"{cfg.eval.agreement.seed}); prompt version `{ctx.prompt_version}`.",
        "- The sample is drawn only from postings the first-pass model answered validly (about "
        "98% of all). The few it failed on, probably the hardest, are excluded, so agreement "
        "here is likely higher than it would be over everything.",
        f"- Spent on this comparison so far: **${spent:.4f}** "
        f"(cap ${cfg.eval.agreement.cap_usd:.2f}).",
    ]
    if stats:
        lines.append(
            f"- Last run: ok {stats['ok']}, invalid output {stats['invalid']}, API errors "
            f"{stats['error']}, not run because of the cap {stats['not_run_cap']}, already done "
            f"{stats['skipped_done']}."
        )
    lines += [
        "",
        "Null (not stated) agreeing with null counts as agreement in the first column; the second "
        "column looks only at postings where at least one model returned a value, which is the "
        "stricter view.",
        "",
        "| Field | Agree (all compared) | Agree (at least one model gave a value) |",
        "|---|---|---|",
    ]
    names = {
        "seniority": "seniority (exact)",
        "seniority_folded": "seniority (staff/principal/manager/director/executive = senior)",
    }
    for f, d in fields.items():
        lines.append(
            f"| {names.get(f, f)} | {pct(d['agree'], d['n'])} | "
            f"{pct(d['agree_either'], d['either'])} |"
        )
    lines += [
        "",
        "**Skills** (names normalized as in `score.py`; postings where either model listed any): "
        f"mean Jaccard overlap **{100 * sk['mean_jaccard']:.1f}%**; "
        f"share of {cfg.first_pass.id}'s skills also listed by {cfg.escalation.id}: "
        f"**{100 * sk['first_in_second']:.1f}%**; the reverse: "
        f"**{100 * sk['second_in_first']:.1f}%** (n={sk['n']}).",
        "",
    ]
    text = "\n".join(lines)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "agreement_report.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="local DuckDB file instead of MotherDuck")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="show sample and cost estimate only")
    mode.add_argument("--run", action="store_true", help="call the escalation model (paid, capped)")
    mode.add_argument("--report", action="store_true", help="write the report from stored results")
    ap.add_argument("--out-dir", default=str(REPORTS_DIR))
    args = ap.parse_args()
    ctx = open_ctx(args.db)
    if ctx is None:
        return 2
    store = Store(ctx.con, ctx.pipeline.warehouse, ctx.ecfg, ctx.prompt_version)
    if args.report:
        print(write_report(ctx, store, None, Path(args.out_dir)))
        return 0
    postings = sample_postings(ctx, store)
    if not postings:
        print("error: no first-pass extractions for the current prompt version to sample from")
        return 2
    est = estimate(ctx, postings, store.agreement_done(ctx.ecfg.escalation.id))
    print_estimate(ctx, est)
    if args.dry_run:
        return 0
    load_env_keys([ctx.ecfg.api_key_env_var])
    import os

    key = os.environ.get(ctx.ecfg.api_key_env_var)
    if not key:
        print(f"error: set {ctx.ecfg.api_key_env_var} (env or .env)", file=sys.stderr)
        return 2
    with HttpClient(ctx.pipeline.http) as http:
        stats = run_agreement(store, ctx, OpenAIClient(ctx.ecfg, http, key), postings)
    print(f"run finished: {Counter({k: v for k, v in stats.items()})}")
    print(write_report(ctx, store, stats, Path(args.out_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
