# Benchmark workflow

**Labeling app (recommended):** from the repo root, after copying the two templates to
`eval/labels.csv` and `eval/sponsorship_labels.csv`:

    uv run streamlit run eval/label_app.py

It shows one posting at a time (keywords highlighted), dropdowns for the enum fields, text inputs
for salary and skills, and **Save & Next** (writes the row back with `done=y`, atomically, keeping
`<file>.bak` of the previous version). The sidebar has a progress counter, a "first 100 rows"
counter, Prev/Next, "Go to next unlabeled", and a jump list (✓ = labeled) to reopen any row. It
reads only the two label sheets and refuses any file with "prediction" in its name. Do not have the
CSV open in a spreadsheet at the same time.

1. `uv run python eval/sample.py` draws 200 postings (stratified, seeded) from the title-filtered
   set and writes `labels_template.csv` and `sample_manifest.json`. It refuses to redraw without
   `--force`. Rows are in a seeded, prefix-balanced order: any first N rows hold every stratum in
   about its full-sample share, so scoring after 100 rows is still representative.
   `sample.py --reorder` re-applies the order without redrawing (refused once labels exist).
2. Copy `labels_template.csv` to `labels.csv` and label it **blind**: the sheet contains only the
   posting id, title, company and description text. Do not open `predictions.csv` meanwhile.
3. Label columns (leave a cell empty when the posting does not say):
   - `seniority`: intern, junior, mid, senior, staff, principal, manager, director, executive
   - `role_family`: data_analyst, data_engineer, analytics_engineer, bi, business_analyst,
     data_scientist, ml_ai_engineer, other
   - `remote_policy`: remote, hybrid, onsite
   - `salary_min`, `salary_max`: plain numbers; `salary_currency`: e.g. USD;
     `salary_period`: hour, day, week, month, year (advertised pay only)
   - `sponsorship_statement`: `offered` only if the posting explicitly says it sponsors;
     `not_offered` only if it explicitly says it does not; otherwise leave empty
   - `skills`: semicolon-separated tools and technologies named in the posting
   - `done`: set to `y` when the row is finished (only these rows are scored)
4. `uv run python eval/export_predictions.py` writes the model output to `predictions.csv`
   (separate file, gitignored).
5. `uv run python eval/score.py` joins labels and predictions by `posting_key` and writes
   `reports/extraction_report.md` and `reports/sponsorship_report.md`.

## Where things stand (2026-11-11)

Manual labeling stopped at 38 rows; `labels.csv` is kept as the **hand-labeled pilot**. Score it
with `export_predictions.py` then `score.py` (rows without `done=y` are skipped), and read
`pilot_analysis.py` for the seniority/skills error analysis. Seniority is reported raw and with
staff/principal/manager/director/executive folded into senior; skills raw, name-normalized, and
name-normalized with competencies excluded (settings: `config/extraction.yaml`, `eval:`).

Automated evaluations that need no human labels, each named for what it measures (none is
accuracy): `salary_crosscheck.py`, `grounding_report.py`, and `agreement.py --dry-run | --run`
(cross-model agreement; `--run` is paid and capped, see its docstring). The 100-posting agreement
run was done on 2026-11-11 ($0.27); its report is `reports/agreement_report.md`.

## Targeted sponsorship sample (separate file)

Only about 2.6% of postings state a sponsorship position, so the random 200 holds few positives.
`uv run python eval/sponsorship_sample.py` adds a second blind sheet,
`sponsorship_labels_template.csv`, with the in-scope postings whose text mentions sponsor, visa,
work authorization or H-1B (pattern in `config/extraction.yaml`), excluding anything already in
the main 200. It has ONE label column, `sponsorship_statement`. Here a blank cell means "not
labeled yet", so write `offered`, `not_offered` or `unspecified` explicitly. Copy it to
`sponsorship_labels.csv`. `score.py` uses it only in the sponsorship report (random-sample
section, targeted-sample section, pooled view) and never in the per-field report.
