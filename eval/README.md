# Benchmark workflow

1. `uv run python eval/sample.py` draws 200 postings (stratified, seeded) from the title-filtered
   set and writes `labels_template.csv` and `sample_manifest.json`. It refuses to redraw without
   `--force`.
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
