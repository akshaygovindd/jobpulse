# JobPulse — Roadmap, Risks, Runbook

> Related: [SPEC.md](SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md)
>
> Priority: get daily raw ingestion running this week so history starts accumulating. Build a thin slice before adding breadth. A phase is finished only when every one of its "done when" checks passes.

## P1: Infra + daily raw ingestion (Greenhouse, Lever). ✅ Done 2026-11-10

**Build**

*Terraform*
- `infra/terraform/bootstrap/`: the state bucket in `us-east-1`.
- `infra/terraform/main/`:
  - data bucket with versioning, SSE-S3 and Block Public Access, and no lifecycle transitions
  - GitHub OIDC provider
  - ingest role, using the exact trust policy in ARCHITECTURE.md
  - AWS Budgets $10/month alert by email (an alert, not a cap)
- Claude writes the code and runs `fmt`/`validate`. The owner runs `plan`/`apply` locally.

*GitHub*
- A `prod` environment whose deployment branch is restricted to `main`.
- The role ARN stored as an environment variable on `prod`.

*Employers*
- The owner supplies the seed list (30–50 employers). Claude converts it to `config/employers.yaml`.
- `scripts/validate_employers.py` probes each board id and records the result.

*Code*
- Shared retry HTTP client.
- Greenhouse and Lever connectors.
- Manifest writer and authoritative-pointer writer.
- Ops JSON writer (`pipeline_runs`, `company_snapshots`, `source_registry`) writing to S3 `ops/`.
- Run report. `scripts/check_freshness.py`.
- **No MotherDuck in P1.**

*Workflows*
- `ci.yml`: ruff, mypy, pytest, terraform fmt/validate, gitleaks.
- `daily_ingest.yml`: cron + dispatch, `environment: prod`, concurrency group.

**Done when**
- [x] CI is green, including gitleaks.
- [x] The owner has applied Terraform, and the budget exists with the owner's email as a subscriber. AWS Budgets email has no opt-in step, so verify the subscriber in the console or with `aws budgets describe-subscribers-for-notification`.
- [x] A manual dispatch writes authoritative pointers for ≥ 90% of seeded companies.
- [x] The run report (`ops/run_reports/…`) and the GitHub step summary list **every failed company with its reason code**. A deliberately bad board id appears there, and the run still succeeds.
- [x] `ops/` contains `pipeline_runs`, `company_snapshots`, and `source_registry` JSON for the run.
- [x] Idempotency tests pass. These are moto-backed unit tests plus one live check:
  - (i) **Re-run of the same workflow run (attempt 2):** writes a new raw attempt prefix, and there is still exactly one pointer per (source, company, date). The pointer moves to attempt 2 only if attempt 2 is complete.
  - (ii) **New dispatch on the same date:** same result as (i).
  - (iii) **Incomplete later attempt:** the pointer is unchanged.

- [x] **Tracked separately (closed 2026-11-11).** The scheduled run succeeds on 2 consecutive days: Daily ingest #7 (Nov 10, 17:04 UTC) and #8 (Nov 11, 17:09 UTC), both `schedule` events, all jobs green. Observed through the GitHub Actions API; the owner may re-confirm in the UI. GitHub started both about 8 h after the 09:17 UTC cron.

## P2: Ashby, bronze/silver, dedup, observation tracking. ✅ Done 2026-11-10

> Adzuna was moved to [Later](#later-deferred) on 2026-11-09 because it now requires a paid plan.

**Build**
- Ashby connector, with the 5 `phase2_ashby` employers moved into `employers`.
- MotherDuck, plus `load_ops.py` to load the S3 ops JSON.
- Bronze loader that reads authoritative pointers only.
- dbt project with silver models: identity, observations, versions, closure, and the cross-source `probable_duplicate_of` flag. The column stays even though only ATS sources with distinct employers are live; it matters once more sources are added.
- `daily_ingest.yml` runs ops load, bronze load and silver build after ingest.

**Done when**
- [x] dbt tests pass (50 tests; green in pytest and on MotherDuck, 2026-11-09):
  - unique (source_id, external_posting_id) among active postings
  - unique (source_id, board_id, snapshot_date, external_posting_id) in bronze
  - not_null on all keys
- [x] Fixture tests prove (`tests/unit/test_bronze_silver.py`):
  - (a) absent from 1 authoritative snapshot → still active
  - (b) absent from 2 consecutive authoritative snapshots → closed
  - (c) a date with no pointer between them → not closed
  - (e) content change → new version row, same posting_key
  - (f) pointer replaced on the same day → bronze and silver row counts unchanged
- [x] Running the full pipeline twice for the same date leaves row counts unchanged (fixture test, plus a live double run on MotherDuck checked with `scripts/warehouse_counts.py`; only `ops.pipeline_runs` grows, because each run records itself).
- [x] The dispatched Daily ingest run (#5, `gha-42738644998-1`) has a green `warehouse` job, with Ashby companies in bronze (verified 2026-11-10).

## P3: Extraction + benchmark. ✅ Done 2026-11-11 (benchmark scope revised by the owner; sponsorship accuracy still unmeasured)

**Build**
- Pydantic extraction schema.
- **Provider changed to OpenAI (2026-11-09):** `gpt-6-luna` runs first and escalates once to `gpt-6.1-terra` when validation fails. Provider, model ids and prices live in `config/extraction.yaml`.
- **Scope for cost:** only postings in target role families (title filter in config, applied in silver before any LLM call) are extracted. The pass count is reported by `scripts/extraction_counts.py`.
- Batch API path built (50% off, `extract --batch submit|collect`); the real backfill ran synchronously by owner choice (cost difference was about $0.08). Budget: $4.96 prepaid; backfill capped at $1.50 per run.
- Content-hash cache.
- Quarantine.
- `llm_calls` logging.
- Per-run spend cap. Once the cap is hit, remaining postings go to the queue.
- `eval/labels_template.csv` (**blind**: id, title, company, description only, no predictions), stratified `eval/sample.py` (200 postings from the filtered set), `eval/export_predictions.py` (predictions in a separate file), `eval/score.py` (joins the two by posting_key), local labeling app `eval/label_app.py`.
- **Revised 2026-11-11 (owner):** manual labeling stopped at **38 rows**, kept as a *hand-labeled pilot*. Further evaluation needs no human labels and is reported separately, each report named for what it measures: `eval/salary_crosscheck.py` (agreement between a regex parser and the model), `eval/grounding_report.py` (validation/quote-in-text pass rates), `eval/agreement.py` (cross-model agreement). None of these is an accuracy measure. Scoring-time conventions (seniority folding, skills name normalization) are configured in `config/extraction.yaml` `eval:` and always shown next to the raw numbers.

**Done when**
- [x] A second run over the same postings makes 0 LLM calls (all cache hits). Verified on the live backfill 2026-11-10: 495 cache hits, 0 calls, $0.
- [x] With a tiny cap, extraction stops, the remaining postings are queued, and no raw data is lost. Proven by `tests/unit/test_extraction.py` (cap tests). Not demonstrated live, because nothing is pending to extract.
- [x] Tests cover the path invalid output → escalation → quarantine (`tests/unit/test_extraction.py`).
- [x] Cost per 1k postings is reported by script: **$0.39** (`scripts/report_extraction_cost.py`; escalation 2.2%, quarantine 0%).
- [x] **Hand-labeled pilot (n=38)**: `eval/score.py` reports per-field precision/recall, with a **separate sponsorship report**. Caveats: n is small; the pilot holds 1 `not_offered` and 0 `offered` sponsorship labels, so sponsorship accuracy is effectively **unmeasured** (the 13-row targeted sponsorship sheet is unlabeled). *The original check (200 owner labels) is not met; it was dropped by the owner on 2026-11-11.*
- [x] **Rule-based salary cross-check** over all extractions with posting text (489 of 503): `eval/salary_crosscheck.py`. Agreement, not accuracy.
- [x] **Validation and grounding pass rates**: `eval/grounding_report.py`. Self-consistency, not accuracy.
- [x] **Cross-model agreement** on a seeded sample of 100 postings (cap $0.40): `eval/agreement.py`. Run 2026-11-11: 100/100 valid, cost **$0.2687**. Consistency, not accuracy: role_family 96%, remote_policy 93%, seniority 88% exact / 89% folded, salary amounts 100%, skills overlap (Jaccard) 65.7%; `salary_period` only 66% because terra often leaves the period empty. Details in `eval/reports/agreement_report.md`.
- [x] **Missing salary period fixed deterministically** (owner decision 2026-11-11): when an amount exists and the period is empty, `year` if the amount is >= 20,000, `hour` if < 500, otherwise empty; flagged `salary_period_inferred`; no API calls, no re-extraction (`extract/period.py`). Applied to 73 stored rows. The pilot's `salary_period` recall goes from 75.0% (model output only) to 96.4% (pipeline output), and both rows are reported.

**Known gaps (not blocking, recorded honestly):** sponsorship accuracy is effectively unmeasured (the pilot has 1 `not_offered` and 0 `offered` label; the 13-row targeted sheet is unlabeled, to be done by the owner later); skills recall stays near 49% against the owner's labels because the owner labels concepts and competencies that the tools-only prompt does not ask for; n=38 is small.

## P4: dbt gold + snapshots + local Airflow/Docker

**Done when**
- [ ] All gold models and `snap_postings` build, with grain and `accepted_values` tests.
- [ ] `dbt build` runs against fixtures in CI.
- [ ] `docker compose up` runs the Airflow DAG end to end against the dev target.
- [ ] Both environments are documented in ARCHITECTURE.md.

## P5: Streamlit MVP (5 pages) + visa enrichment

**Build**
- LCA loader for the selected fiscal years.
- Employer name normalization, with a match confidence score.
- Visa evidence classes.
- **MVP pages:** Market Overview, Hiring Trends, Salary, Visa Intelligence, Pipeline Health.

**Done when**
- [ ] Pages read only from gold and ops tables.
- [ ] Salary shows coverage %, and advertised wages and LCA wages appear in separate views.
- [ ] Visa pages show the evidence fields and the "evidence, not a promise" disclaimer.
- [ ] Employer-match precision on ≥ 50 labeled employers is reported by script.
- [ ] Streamlit AppTest smoke tests run in CI.
- [ ] Pipeline Health shows a freshness banner when any source is more than 30h stale.

## P6: Resume Match + deploy

**Build**
- Parse the resume in memory. Keep only skills, titles, years of experience, and location preference in session state.
- Never store the file or the raw text.
- Add a **Delete** control.
- Sponsorship works as a filter only and never feeds into the score.

**Done when**
- [ ] Tests prove nothing from the resume is written to disk or the database.
- [ ] Delete is verified by a test.
- [ ] The app is deployed on Streamlit Community Cloud using a read-only MotherDuck token.
- [ ] The README has the live link and the demo script.

## P7: Agent (later)

**Build**
- Neon Postgres with pgvector, and RAG on top of it.
- Guarded text-to-SQL:
  - read-only role
  - allow-listed tables
  - statement timeout
  - row limit
  - refusal cases
- 50-question benchmark that includes refusals.
- USAJobs (federal flag) and Remotive (required attribution) also land here.

**Done when**
- [ ] The benchmark script reports accuracy and refusal correctness.
- [ ] Red-team prompts are refused: DDL/DML, tables not on the allow-list, PII.

## Later (deferred)

### Adzuna (deferred 2026-11-09: now requires a paid plan)
Design notes kept for when it is revisited. Adzuna is used for market breadth only, never for closure.
- API credentials as GitHub `prod` environment secrets.
- Pagination.
- Daily quota budget from config, with a clean stop when it is reached.
- Attribution.
- Snapshot unit = search slice, so the logical snapshot is (source, search slice, `snapshot_date`).
- `supports_closure: false` in `config/sources.yaml`.
- Fixture test to add with it: (d) absent from Adzuna → never closed.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| **GitHub disables scheduled workflows after 60 days with no repo activity (public repos)** | **No keepalive workflow**, since none could be shown to work reliably. Any commit resets the clock, and active development covers the build phases; the real exposure is after the project is finished. **Detection:** `check_freshness.py` fails the daily job when any source is more than 30h stale. It catches partial staleness but **cannot fire if the workflow itself is disabled**. The Pipeline Health freshness banner (P5) and a periodic manual check (runbook below) cover that case. **Recovery:** see runbook. |
| GitHub Actions cron delayed or dropped | Schedule at an off-peak minute (`17 9 * * *`). The pointer per date is idempotent. `workflow_dispatch` takes a `snapshot_date`. |
| Duplicate or competing attempts on the same day | Attempts are append-only. Each (source, company, date) has a single authoritative pointer, guarded by a concurrency group and a `completed_at` check. Covered by tests (i)–(iii) and (f). |
| Board id changes or employer switches ATS vendor | Validation script; per-company isolation; reason codes in the run report |
| API schema drift | Raw payloads are always preserved. Drop reasons are counted. Each connector has fixture tests. |
| False closures | A posting closes only after 2 consecutive authoritative snapshots without it. A date without a pointer breaks the streak and is never counted as an absence. Covered by tests (a)–(c). Adzuna, when added later, is excluded from closure. |
| Adzuna quota, terms or cost (deferred) | Now requires a paid plan, so it is deferred. When revisited: quota budget from config, clean stop, attribution, market breadth only. |
| LLM cost overrun or hallucinated fields | Per-run spend cap; content-hash cache; cheap model first; original text preserved; confidence field; quarantine; precision/recall eval |
| AWS cost surprise | $10/month Budgets alert (an alert, not a cap). Small footprint. No Standard-IA transitions. |
| Sponsorship misread as a promise | Evidence classes; disclaimer on every visa view; sponsorship excluded from match score; LCA match confidence shown |
| LCA employer name mismatch | Normalized and fuzzy matching with a confidence score; threshold set in config; labeled match sample |
| Secret leakage in a public repo | OIDC (no AWS keys); environment secrets; gitleaks in CI; example files only; `*.tfvars` and `.env` gitignored |
| OIDC misuse from branches or PRs | `sub` pinned to `environment:prod`; `prod` restricted to `main` |
| Resume privacy | No persistence; minimal fields; Delete; enforced by tests |
| MotherDuck outage or free-tier limits | S3 is the source of truth. Ingestion never depends on MotherDuck. Ops and bronze can be rebuilt from S3. |
| Cross-source duplicates inflate counts | `probable_duplicate_of` flag; flagged rows excluded from market counts; never merged |

## Runbook: scheduled workflow disabled or data stale

1. **Check.** Look at the Actions tab or the Pipeline Health freshness banner. Do this at least monthly once active development stops.
2. **Re-enable.** Actions tab → **Daily ingest** → **Enable workflow**.
3. **Run.** **Run workflow** on `main` with `snapshot_date` = today (UTC).
4. **Verify.**
   - The run report lists any failed companies.
   - `scripts/check_freshness.py` passes.
   - New pointers exist under `snapshots/…/dt=<today>/`.
5. **Record.** Add the gap dates to NOTES.md. Missed days can't be observed after the fact, so they stay gaps. Never backfill them as if they had been observed. Closure logic already skips dates without pointers.
