"""Score extraction against the owner's hand labels: per-field precision/recall, plus a
SEPARATE sponsorship report.

    uv run python eval/score.py [--labels eval/labels.csv] [--predictions eval/predictions.csv]

Labels (blind sheet filled in by the owner) and predictions (a separate file) are joined by
posting_key. Only rows with done=y are scored. A blank label cell or "unspecified" means the
field is absent from the posting. A posting with no prediction (quarantined or missing) scores as
"predicted nothing": it lowers recall and is reported as coverage.

Definitions, per field: a prediction is a non-empty value. TP = prediction equals the label;
FP = prediction present and not equal (including when the label is empty); FN = label present
and the prediction is absent or different. Precision = TP/(TP+FP), recall = TP/(TP+FN).
Skills are scored as a micro-averaged set comparison (lower-cased); salary numbers match within
`SALARY_TOLERANCE`.

Two scoring-time conventions (owner decision 2026-11-11, from the 38-row pilot), both configured
in config/extraction.yaml and both reported NEXT TO the raw exact-match number:
- seniority (folded): staff/principal/manager/director/executive count as senior on both sides.
- skills (normalized): lower-case, drop parenthetical abbreviations, drop a leading "Apache", strip
  punctuation and a plural "s". skills (tools only): additionally drop competencies and soft skills
  (e.g. "stakeholder management") from BOTH sides, since the prompt asks for tools and technologies.
Stored extractions are never changed.
- salary_period: the pipeline fills a missing period from the amount (see
  config/extraction.yaml salary_period_inference). The headline row is the pipeline's output; a
  second row drops the inferred values so the model's own output can be read separately.

Targeted sponsorship sample (optional): if `eval/sponsorship_labels.csv` exists, its labeled rows
(sponsorship_statement filled in; blank = not labeled yet) are scored ONLY in the sponsorship
report, as a second section next to the random-200 results plus a pooled view. They never enter
the per-field report. The targeted sample is enriched for sponsorship mentions, so its accuracy and
the pooled numbers do not describe the prevalence in the whole data; only the random sample does.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path
from typing import Any

from common import (
    LABELS_PATH,
    PREDICTIONS_PATH,
    REPORTS_DIR,
    SPONSOR_LABELS_PATH,
    SPONSOR_PREDICTIONS_PATH,
    SPONSOR_VALUES,
)

SALARY_TOLERANCE = 0.005  # relative
from jobpulse.config import ExtractionConfig, load_extraction  # noqa: E402

CATEGORICAL = ["seniority", "role_family", "remote_policy", "salary_currency", "salary_period"]
NUMERIC = ["salary_min", "salary_max"]
SPONSOR_CLASSES = ["offered", "not_offered", "unspecified"]
MIN_SUPPORT = 5  # classes with fewer labeled examples are flagged as low-support

Row = dict[str, str]


def _blank(v: str | None) -> bool:
    return v is None or v.strip() == "" or v.strip().lower() == "unspecified"


def _cat(v: str | None) -> str | None:
    return None if _blank(v) else str(v).strip().lower()


def _num(v: str | None) -> float | None:
    if v is None or v.strip() == "":
        return None
    try:
        return float(v.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _skills(v: str | None) -> set[str]:
    if not v:
        return set()
    return {s.strip().lower() for s in v.replace("|", ";").split(";") if s.strip()}


def fold_seniority(v: str | None, fold: dict[str, str]) -> str | None:
    c = _cat(v)
    return fold.get(c, c) if c else None


def norm_skill(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s*\([^)]*\)", "", s)  # "natural language processing (nlp)" -> "... processing"
    s = re.sub(r"^apache\s+", "", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9+#./ ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    words = [
        w[:-1] if len(w) >= 4 and w.endswith("s") and not w.endswith(("ss", "is", "us")) else w
        for w in s.split()
    ]
    return " ".join(words)


def _norm_set(skills: set[str], out_of_scope: re.Pattern[str] | None = None) -> set[str]:
    kept = {s for s in skills if not (out_of_scope and out_of_scope.search(s))}
    return {n for n in (norm_skill(s) for s in kept) if n}


def _same_num(a: float, b: float) -> bool:
    return abs(a - b) <= SALARY_TOLERANCE * max(abs(a), abs(b), 1.0)


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def read_csv(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def join(labels: list[Row], preds: list[Row]) -> tuple[list[tuple[Row, Row | None]], int]:
    """Labeled rows (done=y) paired with their prediction row (None when absent)."""
    by_key = {p["posting_key"]: p for p in preds}
    pairs: list[tuple[Row, Row | None]] = []
    skipped = 0
    for lab in labels:
        if lab.get("done", "").strip().lower() != "y":
            skipped += 1
            continue
        p = by_key.get(lab["posting_key"])
        pairs.append((lab, p if p and p.get("status") == "extracted" else None))
    return pairs, skipped


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
    }


def score_fields(
    pairs: list[tuple[Row, Row | None]], ecfg: ExtractionConfig | None = None
) -> dict[str, dict[str, Any]]:
    cfg = (ecfg or load_extraction()).eval
    fold = cfg.seniority_fold
    scope = re.compile(cfg.skills_out_of_scope_pattern, re.IGNORECASE)
    out: dict[str, dict[str, Any]] = {}
    for f in CATEGORICAL:
        tp = fp = fn = 0
        for lab, pred in pairs:
            g, p = _cat(lab.get(f)), _cat(pred.get(f) if pred else None)
            if p is not None:
                tp, fp = (tp + 1, fp) if p == g else (tp, fp + 1)
            if g is not None and p != g:
                fn += 1
        out[f] = _prf(tp, fp, fn)
    for f in NUMERIC:
        tp = fp = fn = 0
        for lab, pred in pairs:
            g, p = _num(lab.get(f)), _num(pred.get(f) if pred else None)
            match = g is not None and p is not None and _same_num(g, p)
            if p is not None:
                tp, fp = (tp + 1, fp) if match else (tp, fp + 1)
            if g is not None and not match:
                fn += 1
        out[f] = _prf(tp, fp, fn)
    tp = fp = fn = 0
    for lab, pred in pairs:
        g, p = _skills(lab.get("skills")), _skills(pred.get("skills") if pred else None)
        tp, fp, fn = tp + len(g & p), fp + len(p - g), fn + len(g - p)
    out["skills"] = _prf(tp, fp, fn)
    tp = fp = fn = 0
    for lab, pred in pairs:
        g = fold_seniority(lab.get("seniority"), fold)
        p = fold_seniority(pred.get("seniority") if pred else None, fold)
        if p is not None:
            tp, fp = (tp + 1, fp) if p == g else (tp, fp + 1)
        if g is not None and p != g:
            fn += 1
    out["seniority_folded"] = _prf(tp, fp, fn)
    tp = fp = fn = 0
    for lab, pred in pairs:
        g = _cat(lab.get("salary_period"))
        inferred = bool(pred) and (pred or {}).get("salary_period_inferred", "").lower() == "true"
        p = None if inferred or not pred else _cat(pred.get("salary_period"))
        if p is not None:
            tp, fp = (tp + 1, fp) if p == g else (tp, fp + 1)
        if g is not None and p != g:
            fn += 1
    out["salary_period_model_only"] = _prf(tp, fp, fn)
    for key, flt in (("skills_normalized", None), ("skills_tools_only", scope)):
        tp = fp = fn = 0
        for lab, pred in pairs:
            g = _norm_set(_skills(lab.get("skills")), flt)
            p = _norm_set(_skills(pred.get("skills") if pred else None), flt)
            tp, fp, fn = tp + len(g & p), fp + len(p - g), fn + len(g - p)
        out[key] = _prf(tp, fp, fn)
    return out


def score_sponsorship(pairs: list[tuple[Row, Row | None]]) -> dict[str, Any]:
    matrix: Counter[tuple[str, str]] = Counter()
    for lab, pred in pairs:
        g = _cat(lab.get("sponsorship_statement")) or "unspecified"
        p = (_cat(pred.get("sponsorship_statement")) if pred else None) or "unspecified"
        matrix[(g, p)] += 1
    per_class: dict[str, dict[str, Any]] = {}
    for c in SPONSOR_CLASSES:
        tp = matrix[(c, c)]
        fp = sum(matrix[(g, c)] for g in SPONSOR_CLASSES if g != c)
        fn = sum(matrix[(c, p)] for p in SPONSOR_CLASSES if p != c)
        d = _prf(tp, fp, fn)
        d["support"] = tp + fn
        d["low_support"] = d["support"] < MIN_SUPPORT
        per_class[c] = d
    total = sum(matrix.values())
    correct = sum(matrix[(c, c)] for c in SPONSOR_CLASSES)
    return {
        "n": total,
        "accuracy": _ratio(correct, total),
        "matrix": {f"{g}->{p}": n for (g, p), n in sorted(matrix.items())},
        "per_class": per_class,
    }


def join_targeted(
    labels: list[Row], preds: list[Row]
) -> tuple[list[tuple[Row, Row | None]], int, list[str]]:
    """Targeted-sample rows with a filled-in sponsorship_statement, paired with predictions.
    Returns (pairs, unlabeled_count, invalid_values)."""
    by_key = {p["posting_key"]: p for p in preds}
    pairs: list[tuple[Row, Row | None]] = []
    unlabeled = 0
    invalid: list[str] = []
    for lab in labels:
        v = lab.get("sponsorship_statement", "").strip().lower()
        if not v:
            unlabeled += 1
            continue
        if v not in SPONSOR_VALUES:
            invalid.append(f"{lab['posting_key']}: {v!r}")
            continue
        p = by_key.get(lab["posting_key"])
        pairs.append(
            ({"sponsorship_statement": v}, p if p and p.get("status") == "extracted" else None)
        )
    return pairs, unlabeled, invalid


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


FIELD_LABELS = {
    "salary_period": "salary_period (pipeline output: model + deterministic fill)",
    "salary_period_model_only": "salary_period (model output only, inferred values dropped)",
    "seniority": "seniority (raw exact match)",
    "seniority_folded": "seniority (staff/principal/manager/director/executive = senior)",
    "skills": "skills (raw exact match)",
    "skills_normalized": "skills (normalized names)",
    "skills_tools_only": "skills (normalized, competencies/soft skills excluded)",
}


def render_main(
    fields: dict[str, dict[str, Any]], n: int, skipped: int, n_pred: int, preds_path: str
) -> str:
    cov = _ratio(n_pred, n)
    lines = [
        f"# Hand-labeled pilot: per-field precision / recall (n={n})",
        "",
        f"- Hand-labeled postings scored: **{n}** (rows without done=y skipped: {skipped}). The "
        "sheet is in a prefix-balanced order, so these are a representative subset of the "
        "stratified sample, but n is small: treat percentages as rough.",
        "- Labels are the owner's. Each field is shown raw; the folded/normalized rows apply the "
        "scoring-time conventions in the `eval/score.py` docstring and are labeled as such.",
        f"- Prediction coverage (extracted, not quarantined/missing): **{_pct(cov)}** "
        f"({n_pred}/{n}); uncovered postings count as FN for every labeled field.",
        f"- Predictions file: `{Path(preds_path).name}`. Produced by `eval/score.py`.",
        "",
        "| Field | Precision | Recall | TP | FP | FN |",
        "|---|---|---|---|---|---|",
    ]
    for f, d in fields.items():
        lines.append(
            f"| {FIELD_LABELS.get(f, f)} | {_pct(d['precision'])} | {_pct(d['recall'])} | "
            f"{d['tp']} | {d['fp']} | {d['fn']} |"
        )
    lines += ["", "Sponsorship is reported separately in `sponsorship_report.md`.", ""]
    return "\n".join(lines)


def _class_table(sp: dict[str, Any]) -> list[str]:
    lines = [
        "| Class | Precision | Recall | Support | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for c, d in sp["per_class"].items():
        flag = " (low support)" if d["low_support"] else ""
        lines.append(
            f"| {c}{flag} | {_pct(d['precision'])} | {_pct(d['recall'])} | {d['support']} | "
            f"{d['tp']} | {d['fp']} | {d['fn']} |"
        )
    lines += [
        "",
        "Confusion (label -> prediction):",
        "",
        "| label -> prediction | n |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in sp["matrix"].items()]
    return lines


def render_sponsorship(
    sp: dict[str, Any],
    targeted: dict[str, Any] | None = None,
    pooled: dict[str, Any] | None = None,
    targeted_note: str = "",
) -> str:
    lines = [
        "# Sponsorship extraction (reported separately)",
        "",
        "Visa data is evidence, never a promise. `offered` and `not_offered` require a verbatim "
        "quote from the posting; everything else is `unspecified`.",
        "",
        "## Hand-labeled pilot (first rows of the stratified random sample)",
        "",
        f"- Labeled postings: **{sp['n']}**; accuracy: **{_pct(sp['accuracy'])}**",
        "",
        *_class_table(sp),
        "",
    ]
    if targeted is not None and pooled is not None:
        lines += [
            "## Targeted sample (postings whose text mentions sponsor / visa / work authorization)",
            "",
            f"- Labeled postings: **{targeted['n']}**; accuracy: **{_pct(targeted['accuracy'])}**."
            " Enriched for sponsorship mentions, so this is not the prevalence in the data.",
            targeted_note,
            "",
            *_class_table(targeted),
            "",
            "## Pooled (random + targeted, no posting counted twice)",
            "",
            f"- Labeled postings: **{pooled['n']}**. Use per-class precision/recall only; the "
            "accuracy and class mix are inflated by the targeted sample.",
            "",
            *_class_table(pooled),
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", default=str(LABELS_PATH))
    ap.add_argument("--predictions", default=str(PREDICTIONS_PATH))
    ap.add_argument("--sponsorship-labels", default=str(SPONSOR_LABELS_PATH))
    ap.add_argument("--sponsorship-predictions", default=str(SPONSOR_PREDICTIONS_PATH))
    ap.add_argument("--out-dir", default=str(REPORTS_DIR))
    args = ap.parse_args()
    for p in (args.labels, args.predictions):
        if not Path(p).exists():
            print(f"error: {p} not found")
            return 2
    pairs, skipped = join(read_csv(Path(args.labels)), read_csv(Path(args.predictions)))
    if not pairs:
        print("error: no rows with done=y in the labels file")
        return 2
    n_pred = sum(1 for _, p in pairs if p is not None)
    main_md = render_main(score_fields(pairs), len(pairs), skipped, n_pred, args.predictions)
    sp_random = score_sponsorship(pairs)
    sp_md = render_sponsorship(sp_random)
    sl, spred = Path(args.sponsorship_labels), Path(args.sponsorship_predictions)
    if sl.exists() and any(
        r.get("sponsorship_statement", "").strip() for r in read_csv(sl)
    ):  # a sheet with no labels yet is skipped, so scoring the main sheet alone still works
        if not spred.exists():
            print(f"error: {spred} not found (run eval/export_predictions.py)")
            return 2
        t_pairs, t_unlabeled, t_invalid = join_targeted(read_csv(sl), read_csv(spred))
        if t_invalid:
            print(
                "error: invalid sponsorship_statement values (use offered, not_offered, "
                "unspecified): " + "; ".join(t_invalid[:5])
            )
            return 2
        if t_pairs:
            seen = {r["posting_key"] for r in read_csv(Path(args.labels))}
            overlap = [k for k in (r["posting_key"] for r in read_csv(sl)) if k in seen]
            if overlap:
                print(f"error: {len(overlap)} targeted postings are also in the main sample")
                return 2
            note = f"- Rows still unlabeled (blank): {t_unlabeled}."
            sp_md = render_sponsorship(
                sp_random,
                score_sponsorship(t_pairs),
                score_sponsorship(pairs + t_pairs),
                note,
            )
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "extraction_report.md").write_text(main_md, encoding="utf-8")
    (out / "sponsorship_report.md").write_text(sp_md, encoding="utf-8")
    print(main_md)
    print(sp_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
