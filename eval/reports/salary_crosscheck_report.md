# Rule-based salary cross-check (agreement, not accuracy)

A conservative regex parser (`eval/salary_rules.py`) and the model each read the same posting; this report counts how often they agree. **Neither side is ground truth, and no human labels are used.** The parser can be wrong (e.g. it may read a customer-savings range as pay) and can miss unusual formats, so a disagreement means 'look at this posting', not 'the model is wrong'. Produced by `eval/salary_crosscheck.py`.

- Extractions in the warehouse for the current prompt version: **503**; with posting text still available (scored): **489**.
- Parser found at least one pay range in **67.7% (331/489)**; the model returned a salary for **72.4% (354/489)**.
- Where both found a salary, the model's amounts equal a parsed range in **96.4% (319/331)** of cases (of which overall-envelope answers: 16, period conflicts: 0).
- Where the parser found a range, the model also returned a salary in **100.0% (331/331)** of cases (a low number would mean missed salaries).

| Result | Postings | Share of scored | Meaning |
|---|---|---|---|
| match | 303 | 62.0% (303/489) | model amounts equal a parsed range |
| match_envelope | 16 | 3.3% (16/489) | several ranges parsed; model gives the lowest low and highest high |
| match_period_conflict | 0 | 0.0% (0/489) | amounts equal, explicit pay period differs |
| partial | 7 | 1.4% (7/489) | one bound equals a parsed bound, the other differs |
| mismatch | 5 | 1.0% (5/489) | parser found ranges; model's amounts match none |
| model_missed | 0 | 0.0% (0/489) | parser found a range; model returned no salary |
| model_only | 23 | 4.7% (23/489) | model returned a salary the parser could not find (unverifiable) |
| no_salary_both | 135 | 27.6% (135/489) | neither found a salary |

**Caveat on the headline rate:** the parser was refined once after a first look at its disagreements with the model (an 'overall envelope' class for multi-location postings and sanity bounds per pay period). The first, unrefined run agreed in 91.6% of cases where both found a salary. Whether an envelope answer is right is a judgment call, so it has its own row.

Salary amounts compare with a relative tolerance of 0.005. A posting that lists several ranges (for example by location) agrees if the model matches any of them. Disagreements to spot-check: `salary_crosscheck_disagreements.csv` (12 rows).
