# Pilot error analysis (n=38 hand-labeled postings)

Disagreement between the owner's labels and the model, for the two weakest fields. Disagreement is not always model error: some of it is labeling convention. Produced by `eval/pilot_analysis.py` (rows without done=y skipped: 162).

## Seniority

- Raw exact match: **21 / 38**. After folding director, executive, manager, principal, staff into senior (scoring-time only): **31 / 38**.
- Owner label values used: {'senior': 22, 'staff': 1, 'mid': 11, 'intern': 3, 'unspecified': 1}. No `junior`, `principal`, `manager`, `director` or `executive` labels were used.

Raw disagreements (label -> model):

| label | model | n |
|---|---|---|
| senior | staff | 5 |
| mid | senior | 3 |
| senior | principal | 2 |
| senior | manager | 2 |
| unspecified | senior | 1 |
| mid | manager | 1 |
| mid | junior | 1 |
| senior | director | 1 |
| senior | mid | 1 |

The folding resolves **10** of the 17 raw disagreements. The remaining **7** (not explained by the staff/manager convention):

| title | label | model | years phrase in text |
|---|---|---|---|
| Senior Machine Learning Engineer, Trust | unspecified | senior | 5+ years |
| Sr. Machine Learning Engineer, Growth Science | mid | senior | 4+ years |
| Sr. Machine Learning Engineer, Core Engineering | mid | senior | 4+ years |
| Manager, Site Analytics | mid | manager | 5+ years |
| Sr. Product Manager, Data Engineering | mid | senior | 5+ years |
| Business Intelligence Analyst | mid | junior | 1 year |
| Data Platform Engineer | senior | mid | 5+ years |

- Titles containing Sr./Senior that were labeled `mid`: **3** (the label seems to follow required years of experience there, not the title word). `junior` is never used by the owner, so a model `junior` is always a disagreement.
- Rows where the label is `unspecified` although the title says Senior are possible label slips; check them before trusting the residual numbers.

## Skills

- The owner listed **30.2** skills per posting (min 3, max 55); the model listed **18.4** (min 6, max 39).

| variant | precision | recall | TP | FP | FN |
|---|---|---|---|---|---|
| raw exact match | 71.3% | 43.6% | 500 | 201 | 647 |
| normalized names | 74.8% | 45.7% | 524 | 177 | 623 |
| normalized, competencies/soft skills excluded | 74.6% | 48.8% | 512 | 174 | 538 |

Of the **623** labeled skills the model did not list (normalized names):

- **29%** appear verbatim in the posting text, so the model could have listed them.
- **13%** are competencies/soft skills (`skills_out_of_scope_pattern`), which the tools-only prompt does not ask for.
- **24%** are phrases of three or more words (concepts, not tool names).

Most-missed labeled skills: cross functional collaboration (13), technical leadership (9), stakeholder management (7), model evaluation (7), code review (6), performance optimization (6), production ml system (6), model training (6), data quality (5), data engineering (5), dashboard development (5), statistical analysis (5), business intelligence (5), python (5), machine learning (4).

Most frequent model skills the owner did not list: dashboard (4), machine learning (4), ai (3), testing (3), sql (3), automation (2), genai (2), anomaly detection (2), llm (2), snowflake (2), rag (2), streaming (2), data pipeline (2), realtime system (2), stream data (2).

Reading: normalization barely moves the numbers, so the gap is scope, not spelling. The owner's lists include technical concepts and competencies (model evaluation, performance optimization, cross-functional collaboration); the prompt asks for tools and technologies.

## Salary period (extra finding)

- Postings where the model returned a salary amount but **no period**: **0 of 28** with an amount.
- Cause: validation does not require `salary_period` (or currency) when an amount is present, so such outputs are accepted instead of escalated. Not changed here (it would alter escalation behavior); see NOTES.md.
