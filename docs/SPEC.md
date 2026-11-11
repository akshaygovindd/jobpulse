# JobPulse — Product Spec

> Status: planning. Scope is **frozen**; changes require owner approval.
> Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [ROADMAP.md](ROADMAP.md) · [NOTES.md](NOTES.md)

## Problem

Job seekers, especially visa-dependent ones, can't easily answer four questions:

1. What is being hired for right now?
2. Which skills are rising?
3. What do these roles pay?
4. Which employers have actually sponsored this kind of role?

The data is spread across ATS job boards. It is unstructured, and sponsorship claims in postings are inconsistent or missing. JobPulse is a daily pipeline that collects public postings, extracts structured fields with an LLM, models them in a warehouse, and serves the result in a Streamlit app. Every number in the app can be traced to its source and the run that produced it.

## Users

| User | Need |
|---|---|
| Job seeker (possibly F-1 / OPT / H-1B) | Market view, salary context, sponsorship *evidence*, resume-to-posting match |
| Market analyst / career coach | Trends in skills, roles, locations, and pay over time |
| Reviewer (hiring manager evaluating this project) | Evidence of engineering quality: Pipeline Health, CI, eval report, honest docs |

## 2-minute demo script

| Time | Page | What to show |
|---|---|---|
| 0:00 | **Pipeline Health** | Freshness per source, last run, which companies failed and why, rows in/kept/dropped by reason, LLM cost per run, days of history accumulated |
| 0:20 | **Market Overview** | Active postings by role family and seniority, source mix. Postings flagged as cross-source duplicates are excluded from counts. |
| 0:40 | **Hiring Trends** | Weekly skill demand; pick one rising skill and show its trajectory |
| 1:00 | **Salary** | Advertised ranges by role and city with coverage %. Then the separate LCA offered-wage view. The two datasets are never blended. |
| 1:20 | **Visa Intelligence** | Filter by evidence class. Open one employer to show LCA fiscal year, case status, SOC, worksite, wage basis, and name-match confidence. Banner: "Evidence, not a promise." |
| 1:40 | **Resume Match** (P6) | Upload a resume, see top matches and the skill gap, apply the sponsorship filter, click **Delete** and see the confirmation |
| 1:55 | Close | Eval table (per-field precision/recall) and CI badge |

## Metrics

Every metric is produced by a script in `scripts/` or `eval/`. No number is typed by hand into docs or the app.

| Metric | Definition | Target / note |
|---|---|---|
| Freshness | Hours since last authoritative snapshot, per source (`scripts/check_freshness.py`) | < 30h |
| Run success | % of scheduled runs succeeding; % of seeded companies with an authoritative snapshot per day | Failures listed by company + reason code |
| Duplicates | Duplicate active postings per (source_id, external_posting_id); duplicate daily snapshots per (source, company, date) | 0. Enforced by tests. |
| Closure precision | Spot audit of closed postings against live boards | Reported by script |
| Extraction quality | Per-field precision/recall on a **38-row hand-labeled pilot** (blind labels, `eval/score.py`; **sponsorship reported separately**); plus automated checks that need no labels and are named for what they measure: salary cross-check and grounding pass rate (agreement/self-consistency) and cross-model agreement. None of these is an accuracy figure | Baseline set in P3 |
| Extraction ops | Quarantine rate, escalation rate, cost per 1k postings | From `llm_calls` |
| Coverage | % postings with advertised salary; % with a sponsorship statement; % employers with an LCA match ≥ confidence threshold | Always shown next to the related chart |
| Cloud cost | AWS Budgets alert at $10/month | An alert, not a cap |

## Non-goals

- Scraping HTML pages, or using any source in a way its terms don't allow
- Applying to jobs on a user's behalf
- Predicting sponsorship outcomes, or giving legal or immigration advice
- Blending advertised salary with LCA wages
- Storing resume files or raw resume text
- User accounts or authentication
- Real-time or intraday ingestion
- Merging cross-source duplicates (they are flagged only)

## MVP vs later

| | Scope |
|---|---|
| **MVP (P1–P5)** | Greenhouse, Lever, Ashby ingestion; silver identity and observation tracking; LLM extraction with eval; dbt gold and snapshots; visa enrichment. **Five MVP pages:** Market Overview, Hiring Trends, Salary, Visa Intelligence, Pipeline Health. |
| **P6** | **Resume Match** page; deploy to Streamlit Community Cloud |
| **Later** | Adzuna (deferred 2026-11-09: now requires a paid plan; design notes in ROADMAP) |
| **Later (P7)** | USAJobs (federal flag), Remotive (required attribution), Neon Postgres + pgvector RAG, guarded text-to-SQL, 50-question agent benchmark with refusals |

Phase details and "done when" checks are in [ROADMAP.md](ROADMAP.md).
