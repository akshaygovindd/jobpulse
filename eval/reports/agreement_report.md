# Cross-model agreement (consistency, NOT accuracy)

`gpt-6-luna` (stored first-pass output) vs `gpt-6.1-terra` (re-run now), same prompt, same schema, on a seeded random sample. **Agreement means two models gave the same answer. It does not mean the answer is right: both can be wrong in the same way, and a disagreement does not say which model is wrong.** No human labels are used. Produced by `eval/agreement.py`.

- Postings compared: **100** (sample size 100, seed 20261111); prompt version `2fd688afd45c`.
- The sample is drawn only from postings the first-pass model answered validly (about 98% of all). The few it failed on, probably the hardest, are excluded, so agreement here is likely higher than it would be over everything.
- Spent on this comparison so far: **$0.2687** (cap $0.40).

Null (not stated) agreeing with null counts as agreement in the first column; the second column looks only at postings where at least one model returned a value, which is the stricter view.

| Field | Agree (all compared) | Agree (at least one model gave a value) |
|---|---|---|
| seniority (exact) | 88.0% (88/100) | 87.5% (84/96) |
| role_family | 96.0% (96/100) | 96.0% (96/100) |
| remote_policy | 93.0% (93/100) | 89.6% (60/67) |
| salary_currency | 100.0% (100/100) | 100.0% (68/68) |
| salary_period | 66.0% (66/100) | 35.8% (19/53) |
| salary_min | 100.0% (100/100) | 100.0% (69/69) |
| salary_max | 100.0% (100/100) | 100.0% (69/69) |
| sponsorship_statement | 100.0% (100/100) | 100.0% (4/4) |
| seniority (staff/principal/manager/director/executive = senior) | 89.0% (89/100) | 88.5% (85/96) |

**Skills** (names normalized as in `score.py`; postings where either model listed any): mean Jaccard overlap **65.7%**; share of gpt-6-luna's skills also listed by gpt-6.1-terra: **75.3%**; the reverse: **83.1%** (n=100).
