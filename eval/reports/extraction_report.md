# Hand-labeled pilot: per-field precision / recall (n=38)

- Hand-labeled postings scored: **38** (rows without done=y skipped: 162). The sheet is in a prefix-balanced order, so these are a representative subset of the stratified sample, but n is small: treat percentages as rough.
- Labels are the owner's. Each field is shown raw; the folded/normalized rows apply the scoring-time conventions in the `eval/score.py` docstring and are labeled as such.
- Prediction coverage (extracted, not quarantined/missing): **100.0%** (38/38); uncovered postings count as FN for every labeled field.
- Predictions file: `predictions.csv`. Produced by `eval/score.py`.

| Field | Precision | Recall | TP | FP | FN |
|---|---|---|---|---|---|
| seniority (raw exact match) | 55.3% | 56.8% | 21 | 17 | 16 |
| role_family | 71.1% | 75.0% | 27 | 11 | 9 |
| remote_policy | 76.9% | 87.0% | 20 | 6 | 3 |
| salary_currency | 96.4% | 96.4% | 27 | 1 | 1 |
| salary_period | 95.5% | 75.0% | 21 | 1 | 7 |
| salary_min | 96.4% | 96.4% | 27 | 1 | 1 |
| salary_max | 96.4% | 96.4% | 27 | 1 | 1 |
| skills (raw exact match) | 71.3% | 43.6% | 500 | 201 | 647 |
| seniority (staff/principal/manager/director/executive = senior) | 81.6% | 83.8% | 31 | 7 | 6 |
| skills (normalized names) | 74.8% | 45.7% | 524 | 177 | 623 |
| skills (normalized, competencies/soft skills excluded) | 74.6% | 48.8% | 512 | 174 | 538 |

Sponsorship is reported separately in `sponsorship_report.md`.
