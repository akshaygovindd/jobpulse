{% docs __overview__ %}

# JobPulse

JobPulse is a daily data platform for job-market intelligence on data and AI roles. Every day it
pulls the public job boards of 43 tech employers, tracks each posting over time, reads the data
and AI postings with a small LLM, adds public H-1B filing evidence from the US Department of
Labor, and serves the result in a Streamlit app.

- **Live app:** <https://jobpulse-insights.streamlit.app>
- **Repository:** <https://github.com/akshaygovindd/jobpulse>

## Layers

| Layer | Models | What it holds |
|---|---|---|
| Staging (`stg_`) | `stg_raw_postings`, `stg_snapshot_pointers` | Raw job-board snapshots, read only through the authoritative pointer for each (source, company, day) |
| Silver (`dim_`, `fct_`, `int_`) | `dim_postings`, `dim_posting_versions`, `fct_posting_observations`, `int_posting_closure` | One row per posting, its change history, daily observations, and closure (absent from 2 consecutive authoritative snapshots) |
| Gold (`fct_`, `dim_`, `agg_`, `bridge_`) | `fct_postings`, `dim_company`, `dim_skill`, `bridge_posting_skill`, `agg_*` | What the app reads: open postings with LLM-extracted fields, skills, advertised salary, H-1B filing aggregates, pipeline health |

## Key models

- `fct_postings`: one row per posting with status, role family, seniority, salary and sponsorship text evidence.
- `bridge_posting_skill` and `dim_skill`: skills asked for by each analyzed posting.
- `agg_salary_by_role` and `agg_lca_wage_by_soc_city`: advertised salary and H-1B wages, kept as separate datasets and never blended.
- `agg_lca_company_year`: certified H-1B filings per company and fiscal year. Evidence, never a promise.
- `agg_data_quality` and `fct_pipeline_runs`: the numbers behind the Pipeline health page.

Every model has uniqueness and not-null tests on its grain. Open the lineage graph (bottom right
button) to follow the data from raw snapshots to the app.

{% enddocs %}
