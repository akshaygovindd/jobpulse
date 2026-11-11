# Session Notes

Plain-English log, appended at the end of every working session (see CLAUDE.md).

---

## 2026-11-09: Planning docs

**What changed**
- Added `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `CLAUDE.md`, and this file. There is no application code, Terraform, or workflows yet.
- The plan went through two review rounds. Owner corrections in round 2:
  1. Added a $10/month AWS Budgets alert. It's an alert, not a cap.
  2. Separated the logical daily snapshot (source, company, date) from the execution attempt. Raw is append-only, there is one authoritative pointer per day, and downstream reads pointers only.
  3. OIDC trust is pinned to `repo:akshaygovindd/jobpulse:environment:prod`, and `prod` is restricted to `main`.
  4. Dropped the keepalive workflow. Inactivity is handled with a freshness alert and a manual recovery runbook instead.
  5. Removed the Standard-IA lifecycle transition.
  6. P1 writes ops data as JSON to S3. MotherDuck starts in P2.
  7. Dropped `tf_plan.yml`. The owner runs plan locally.
  8. The "2 consecutive scheduled days" criterion is tracked separately and doesn't block P2. P1 reports failed companies with reason codes.
  9. The five MVP pages (P5) are labeled separately from Resume Match (P6).
  10. The owner provides the seed employer list.
- Owner decisions:
  - Extraction uses Haiku 5.5 first and escalates to Sonnet 5.5, configured in YAML.
  - Region is us-east-1, with a bootstrap state bucket.
  - Cross-source duplicates get a flag column only.
  - CI includes gitleaks.
  - Hosting is Streamlit Community Cloud.

**Checks run**
- None needed. Only docs changed, and no code exists yet.

**Decisions after review**
- The budget alert uses direct email subscribers, not SNS. Because AWS Budgets direct email has no opt-in step, P1 verifies the subscriber in the console or with the CLI.
- Docs were committed on `docs/planning`, merged into `main`, and pushed. From now on, work happens directly on `main`.

**Open questions for owner**
- Waiting on the seed employer list to build `config/employers.yaml`.

---

## 2026-11-10: Phase 1 build (Greenhouse + Lever daily raw ingestion)

**What changed**
- **Employers.** `config/employers.yaml` was built by probing the public Greenhouse, Lever and Ashby APIs for the 50 seed companies, and the owner approved it.
  - 38 Phase 1 employers: 35 on Greenhouse, 3 on Lever.
  - 5 Ashby employers kept in `phase2_ashby` for Phase 2.
  - 7 skipped: Flywire is on Workday; Wayfair, DraftKings, Marqeta, Zillow, Rapid7 and Etsy have no public board.
- **Ingestion code** (`src/jobpulse/`):
  - Shared retrying HTTP client: backoff with jitter, honours 429 `Retry-After` (capped), timeouts, and maps every failure to a reason code.
  - Greenhouse connector: flags a truncated response when `meta.total` doesn't match the number of jobs returned.
  - Lever connector: skip/limit pagination, with checks for pagination drift and a max-pages limit.
  - Snapshot identity: `snapshot_date` must be today's UTC date. `attempt_id` is `gha-<run_id>-<run_attempt>` or `local-<ulid>`.
  - Append-only raw pages: gzip with a deterministic header, sha256 of the canonical JSON stored in object metadata.
  - Manifest written last.
  - Authoritative pointer: pages are re-checked against their hashes, and a later `completed_at` is required to replace an existing pointer.
  - Per-company failure isolation.
  - Ops JSON written to S3 under `ops/`, plus a run report that also goes to the GitHub step summary.
  - Freshness check: a source is stale after 30h.
  - CLI: `jobpulse ingest | check-freshness | validate-employers`.
- **Today-only rule.** Live runs accept only the current UTC date. This is enforced in the workflow, the CLI and the runner, and tested for past, future, today, malformed input, and the UTC-vs-local boundary.
- **Terraform** (`infra/terraform/`):
  - `bootstrap/`: state bucket with local state and `prevent_destroy`.
  - `main/`: data bucket (versioning, SSE-S3, Block Public Access, a TLS-only bucket policy, no lifecycle transitions).
  - GitHub OIDC provider, and a role trusting only `repo:akshaygovindd/jobpulse:environment:prod` with `aud` `sts.amazonaws.com`. The role can only Put/Get under `raw/`, `snapshots/` and `ops/`, and List the bucket.
  - AWS Budgets $10/month alert by direct email: 80% actual, 100% forecasted. It is an alert, not a cap.
  - S3 native state locking.
  - `terraform.tfvars.example` and `backend.hcl.example`; the real files are gitignored.
- **Workflows.**
  - `ci.yml`: ruff, mypy, pytest, terraform fmt/validate, gitleaks.
  - `daily_ingest.yml`: cron at 09:17 UTC plus manual dispatch, `environment: prod`, concurrency group, date gate, freshness step.
- **Other.** Makefile (`make check`). Test fixtures are real API responses, trimmed. ARCHITECTURE.md now lists the full set of reason codes and the today-only rule.

**Checks run**
- `make check`: ruff, ruff format, mypy (strict, 21 files), pytest (60 passed, 2 live tests deselected), terraform fmt + validate for both roots. All green.
- gitleaks isn't installed locally, so it was skipped here. It runs in CI.
- `pytest -m live`: 2 passed against real Greenhouse and Lever.
- Live local ingest to a scratch directory, not S3: 38/38 complete snapshots, 7,337 postings, about 8.6 MB per day compressed, about 8 s.
- A same-day rerun wrote a second attempt prefix and moved the pointer to it.
- `check-freshness` passed.

**Not done by Claude (owner steps)**
- terraform plan/apply.
- GitHub `prod` environment and its variables.
- Budget subscriber check.
- First dispatched run.

P1 isn't done until those steps and the remaining done-when checks pass on the real run (pointers for ≥90% of companies, ops JSON in S3).

**Tracking check (doesn't block P2):** 2 consecutive successful scheduled runs. Dates not yet recorded.

**Open questions for owner**
- None blocking. Optional: install gitleaks locally (`brew install gitleaks`) so `make check` also scans for secrets.

---

## 2026-11-10: OIDC assume-role failure (debugging)

**What happened**
- The first Daily ingest run failed with "Not authorized to perform sts:AssumeRoleWithWebIdentity".

**What changed**
- Added a temporary debug step to `daily_ingest.yml`, before `configure-aws-credentials`.
  - It requests the OIDC token with audience `sts.amazonaws.com` and prints only the decoded `sub`, `aud` and `iss` claims.
  - The raw token is masked and never printed.
  - Remove the step once the trust policy is fixed.

**Finding**
- The unauthenticated GitHub API call `GET /repos/akshaygovindd/jobpulse/actions/oidc/customization/sub` returned:
  - `use_default: true`
  - `use_immutable_subject: true`
  - `sub_claim_prefix: "repo:akshaygovindd@97725846/jobpulse@1451951419"`
- So the real `sub` probably includes the owner and repo numeric IDs, which doesn't match the `repo:akshaygovindd/jobpulse:environment:prod` in the trust policy.
- The trust policy is unchanged until the debug step shows the actual `sub`.

**Checks run**
- `make check`: all green.
- The debug step's claim decoder was tested locally against a fake JWT. It printed only sub, aud and iss.

---

## 2026-11-10: OIDC trust policy fix

**What changed**
- Confirmed the real token `sub`: `repo:akshaygovindd@97725846/jobpulse@1451951419:environment:prod`, with `aud` `sts.amazonaws.com`. The repo uses GitHub's immutable subject format, which embeds the owner and repo IDs.
- Terraform: the trust policy's `sub` condition now uses the new variable `github_oidc_subject` (`StringEquals`), with that exact value as the default and the reason documented in `variables.tf`.
- ARCHITECTURE.md: the trust policy section matches the new `sub` and explains the immutable subject format.
- Removed the temporary DEBUG step from `daily_ingest.yml`.
- README: added the AI-assisted tooling statement.
- From now on, commits no longer carry a Co-Authored-By trailer (owner request).

**Checks run**
- `make check`: all green.

**Owner steps**
- `terraform plan`/`apply` in `infra/terraform/main`. Expect a single in-place update to the role's trust policy.
- Re-run Daily ingest.

---

## 2026-11-10: Phase 1 complete

All P1 done-when checks pass. The results below were verified on AWS using read-only `aws s3` and `aws budgets` calls.

| Check | Result |
|---|---|
| CI | Green, confirmed by the owner. Covers ruff, mypy, pytest, terraform fmt/validate and gitleaks. |
| Terraform + budget | Applied by the owner. Budget `jobpulse-monthly` lists one `EMAIL` subscriber, checked with `describe-subscribers-for-notification`. |
| First run, `gha-42732783751-1` (dispatch) | 38/38 complete, 0 failed, 7,337 postings, about 9 s |
| Rerun, `gha-42733714781-1` (Daily ingest #4: a new dispatch on the same date) | 38/38 complete, 0 failed, 7,339 postings. Owner confirmed no other run was pending. |
| Pointers for 2026-11-10 | Exactly 38, one per company, all pointing at the newer `gha-42733714781-1` |
| Raw history | All 38 companies have 2 append-only attempt prefixes. S3 versioning keeps the previous pointer version. |
| Ops JSON | `pipeline_runs`, `company_snapshots`, `source_registry` and `run_reports` are present for both attempts, plus `source_registry/latest.json` |
| Failed-company reporting | Neither production run had a failure. A live run against the real Greenhouse API with a fake board id, written to a local directory rather than S3, reported it as `board_not_found` while the rest of the run succeeded. Unit tests cover the same case. |

**Idempotency coverage**
- **(ii) New dispatch on the same date:** verified on AWS.
- **(i) "Re-run jobs" on the same workflow run (`run_attempt` 2):** covered by moto unit tests, not repeated on AWS.
- **(iii) Incomplete later attempt leaves the pointer unchanged:** covered by moto unit tests.

**Other fixes during P1**
- **OIDC trust policy:** the repo uses GitHub's immutable subject format, so the trust policy now pins `repo:akshaygovindd@97725846/jobpulse@1451951419:environment:prod`.
- **Commit attribution:** disabled through `.claude/settings.json` (`attribution.commit`/`pr` empty, `sessionUrl` false). Older commits still carry the trailer; history wasn't rewritten.

**Tracking check (still open; doesn't block P2):** the first scheduled run is at 09:17 UTC on 2026-11-10. 2 consecutive successful scheduled days still need recording.

**Next:** P2, which adds Ashby, Adzuna, MotherDuck bronze/silver, dedup and observation tracking.

---

## 2026-11-09: Phase 2 build (Ashby, MotherDuck ops/bronze, dbt silver)

**Scope change.** Adzuna is deferred because it now requires a paid plan. It moved to "Later" in ROADMAP.md and SPEC.md, with its design notes kept (credentials, pagination, daily quota budget with a clean stop, attribution, snapshot unit = search slice, never used for closure). Removed from `config/sources.yaml` so it doesn't show up in `source_registry`. P2 fixture test (d) was Adzuna-specific and is dropped; it comes back with Adzuna.

**What changed**
- **Ashby connector** (`connectors/ashby.py`). The 5 Ashby employers moved from `phase2_ashby` into `employers` (43 boards now). Ashby returns no total, so completeness can't be cross-checked; an empty `jobs` list is reported as `truncated` (config `ashby_allow_empty: false`) so an empty response can never look like "everything closed". Live check: 5/5 boards, 956 postings.
- **Warehouse code.** `warehouse.py` (MotherDuck or a local file; the token is only read from the environment and scrubbed from errors), `ops/load_ops.py`, `bronze/loader.py`, `bronze/normalize.py`, `silver.py`. New commands: `load-ops`, `load-bronze`, `build-silver`.
- **Bronze loader** lists `snapshots/` only and follows each pointer to its manifest, re-verifying hashes. It replaces a company-day's rows in one transaction when the pointer moves. A bad snapshot is recorded with a reason and the rest still load. A test found that a corrupted gzip page raised past the loader; fixed, and now covered.
- **Extra bronze table `snapshot_pointers`.** Closure has to know which dates are authoritative, including empty boards, which have no posting rows.
- **dbt project** (`dbt/`): `stg_*` views and silver tables `dim_postings`, `fct_posting_observations`, `dim_posting_versions`, `int_posting_closure`, `int_posting_duplicates`. 50 tests on grains and keys, including the two done-when uniqueness tests. A custom `unique_combination` test avoids pulling `dbt_utils`.
- **Closure rule as built:** N (config, 2) absences on **calendar-adjacent dates that both have pointers**. A date without a pointer breaks the streak. This is the stricter reading, and it is what test (c) requires.
- **`probable_duplicate_of`:** flag only, same company + normalized title + normalized location in a different source. Will be empty with today's employers; the column and logic stay for new sources.
- **Workflow:** `daily_ingest.yml` now has a second `warehouse` job (`needs: ingest`) running load-ops, load-bronze, build-silver. `MOTHERDUCK_TOKEN` is set only on those steps. The ingest job installs no dbt.
- **DuckDB is pinned to 1.5.6.** The first connection attempt with 2.0.0 was refused by MotherDuck ("latest supported version is v1.5.6").
- `scripts/warehouse_counts.py` produces every warehouse row-count figure quoted here.

**Checks run**
- `make check` after every change. Final: ruff, ruff format, mypy strict (28 files), pytest **96 passed** (3 live deselected), terraform fmt + validate for both roots: all green. gitleaks isn't installed locally (runs in CI).
- `pytest -m live`: 3 passed (Greenhouse, Lever, Ashby).
- Local end to end (scratch dir + local DuckDB): Ashby ingest 5/5, bronze 956 rows, silver 7 models + 50 tests green, run twice with identical data-table counts.
- **Live MotherDuck** (from the owner's machine, real S3 pointers for 2026-11-10): `load-ops` ok; `load-bronze` 38/38 pointers, 7,339 postings; `build-silver` 7 models, 50 tests, 0 failures. Second full pass: 0 bronze loads, data-table counts identical (`bronze.raw_postings` 7,339, `silver.dim_postings` 7,339); only `ops.pipeline_runs` grew, by the bronze and silver run records.
- The 38 loaded pointers are the second attempt (#4) of 2026-11-10, matching what the pointers name.

**Numbers worth watching**
- MotherDuck database size after one day of data: **33.7 MiB** (7,339 postings, no Ashby yet). If every day adds that much, that's roughly 1 GB a month against the free tier. I'll re-measure after a week of daily loads. If it grows linearly, the mitigation is keeping bronze payloads only when `content_hash` changes.
- With a single day of history nothing can be closed yet; the first closures can appear after the second consecutive daily snapshot.
- `ops.source_registry` still has the old 4-row registry (includes Adzuna) until the next ingest rewrites `ops/source_registry/latest.json`.

**Security note**
- While reading `.env` I used shell `source`, which failed on the long value and echoed the `MOTHERDUCK_TOKEN` line into this session's output. Nothing was committed or sent anywhere, and `.env` is gitignored, but the value is now in the session transcript. Recommend rotating the token (see owner steps).

**Open questions for owner**
- Rotate the MotherDuck token? (see above)
- OK to leave `ashby_allow_empty: false`? It means an Ashby board that genuinely has zero openings will show as a failed (truncated) company in the run report instead of an empty snapshot.

**Owner steps**
1. (Recommended) Rotate the MotherDuck token, then update the `prod` secret `MOTHERDUCK_TOKEN` and your local `.env`.
2. After the push, run **Daily ingest** by dispatch. Check that both jobs are green, the run report shows Ashby 5/5, and the `warehouse` job summary lists bronze loaded and silver tests passed. Then P2's last box is done.
3. The P1 tracking check (two consecutive successful scheduled runs) is still open.

---

## 2026-11-10: Phase 2 complete

Daily ingest #5 (`gha-42738644998-1`) verified read-only on S3 and MotherDuck. All P2 done-when checks pass.

| Check | Result |
|---|---|
| Ingest | 43/43 complete, 0 failed, 43 authoritative, 8,296 postings. Ashby 5/5 (956 postings). |
| S3 pointers for 2026-11-10 | Exactly 43 (35 greenhouse, 3 lever, 5 ashby), all naming `gha-42738644998-1`. Record counts sum to 8,296 (6,928 + 412 + 956). |
| Bronze | `snapshot_pointers` 43 (one attempt only), `raw_postings` 8,296, matching S3 per source. Replaced the earlier attempt's rows rather than appending. |
| Warehouse job | `ops.pipeline_runs` has the run's `bronze` rows (success, per source) and a `silver` row (success, 8,296 rows). The silver step only reports success when every dbt model and test passes. |
| Silver | `dim_postings` 8,296 (ashby 956, lever 412, greenhouse 6,928); observations 8,296; versions 8,296. |
| Ops | `company_snapshots` has 43 complete rows for the run; `source_registry` has 3 sources (Adzuna gone). |
| Closure / duplicates | 0 closed, 0 flagged, as expected with one day of history and no overlapping sources. |

- MotherDuck database size: **54.7 MiB** with 8,296 postings of one day. I'll re-measure after a week of daily loads; the mitigation stays "keep bronze payloads only when `content_hash` changes" if growth is too fast.
- `ashby_allow_empty` stays `false` (owner decision).
- Not verified here: the GitHub job log itself, since `gh` isn't installed locally. The green state is inferred from the rows the job wrote and the owner's report.

**Still open:** the P1 tracking check (two consecutive successful scheduled runs). First possible closures appear after the second consecutive daily snapshot.

**Next:** P3, extraction + benchmark.

---

## 2026-11-10: Phase 3 built (extraction + benchmark), paid run not started

**Decisions (owner)**
- Provider is **OpenAI**, not Anthropic. First pass `gpt-6-luna` ($0.10/$0.50 per 1M tokens; Batch $0.05/$0.25), escalation `gpt-6.1-terra` ($1/$5). Prices and Structured Outputs/Batch support checked on developers.openai.com on 2026-11-09 and copied into `config/extraction.yaml`. `gpt-5-nano` is cheaper but an older generation; not chosen.
- Scope for cost: only target role families are extracted, selected by a config title filter in silver (`int_extraction_candidates`) before any LLM call.
- Budget $4.96 prepaid. Backfill must stay under $3. Per-run caps: $0.25 daily, $1.50 backfill/batch.
- Labeling is **blind**: `eval/labels_template.csv` has only posting id, title, company, description text and empty label columns. Predictions are exported to a separate gitignored file (`eval/export_predictions.py`) and `eval/score.py` joins them by `posting_key`.

**What changed**
- `config/extraction.yaml`, `config/extraction_prompt.md`; Pydantic `ExtractionConfig`.
- Shared HTTP client gained POST, multipart upload, per-call headers and timeouts (still the only HTTP path; no new dependency, no `openai` SDK).
- `jobpulse/secrets.py`: reads named keys from `.env` inside Python, never overrides existing env, redacts key shapes from error text. The shell never sources `.env`. `_warehouse()` now uses it for the MotherDuck token too.
- `jobpulse/extract/`: strict schema with grounding checks (quoted salary/sponsorship text must appear in the posting), cost maths, store (extractions = cache keyed on content_hash + schema_version + prompt_version, quarantine, queue, llm_calls, batches), spend-cap budget with reservations, runner (cheap first, one escalation, quarantine, per-posting isolation), Batch submit/collect.
- CLI: `jobpulse extract [--limit N] [--cap USD] [--backfill] [--dry-run] [--batch submit|collect]`. `--dry-run` makes no API call and needs no key.
- Eval: `sample.py` (seeded, stratified by title family x source, refuses to redraw without `--force`), `export_predictions.py`, `score.py` (per-field precision/recall; separate sponsorship report with confusion matrix), `eval/README.md`.
- Scripts: `scripts/extraction_counts.py` (filter pass counts), `scripts/report_extraction_cost.py` (cost per 1k postings, escalation and quarantine rates).
- Docs updated for the provider change: CLAUDE.md stack line, ARCHITECTURE.md, ROADMAP.md, SPEC.md, `.env.example`.

**Numbers (from scripts, live MotherDuck, read-only plus a silver rebuild)**
- `scripts/extraction_counts.py`: **776 of 8,296 active postings pass the title filter (9.4%)**. By family: ml_ai_engineer 515, data_scientist 94, data_analyst 77, data_engineer 67, analytics_engineer 14, business_analyst 8, bi 1. By source: greenhouse 636, ashby 107, lever 33. The ML/AI family is two thirds of the set because `\bAI\b` and "applied/research scientist" match many "Software Engineer, AI" titles; tell me if that is too broad.
- `jobpulse extract --dry-run --backfill`: 776 to extract, avg 6,240 characters. Projected cost **$0.65 with Batch first pass** (first pass $0.19 + escalation $0.46 at the assumed 12% escalation rate), $0.84 fully synchronous. Daily incremental is estimated at $0.02-0.04. These are projections from config assumptions (2,200 input / 550 output tokens, 12% escalation), **not measured**. The calibration run replaces them with real token counts.
- Silver rebuild on MotherDuck: 8 models, 54 tests, 0 failures.

**Checks run**
- `make check` after each change; final: ruff, ruff format, mypy strict (36 files), pytest **125 passed** (3 live deselected), terraform fmt + validate both roots. gitleaks not installed locally (runs in CI).
- New tests (29): title filter; second run = 0 calls; prompt/schema change misses cache; invalid -> escalation -> success; invalid -> escalation -> quarantine and not retried; transport error isolated, not quarantined; truncated output escalates; cap stops the run, queues the rest, bronze/silver counts unchanged, queue drains next run; grounding and structural validation; strict JSON schema; batch submit/collect idempotency incl. escalation and a blocked second submit; batch discount; API error text never contains the key; `.env` loader; blind sheet columns; sample script output and no-redraw guard; scoring and sponsorship report.

**Not verified**
- **No paid OpenAI call has been made.** The local `.env` has no `OPENAI_API_KEY` (only `MOTHERDUCK_TOKEN`), so the 20-posting calibration run could not start. The request/response shapes (Responses API, strict `json_schema`, Batch via `/v1/responses`) follow OpenAI's documented API but are only exercised against mocks until the calibration run.
- The pricing page does not say whether reasoning tokens are billed as output; the estimate assumes they are.
- Done-when items for P3 that need real data are still open: cache re-run on live data, cap run on live data, `score.py` on the owner's 200 labels, cost per 1k from `llm_calls`.

**Known limits**
- If the cap is hit between a first-pass failure and its escalation, the posting is queued and the first-pass spend is repeated next run (rare, cheap).
- A crash between creating a batch and recording it would orphan that batch; the open-batch guard only knows recorded batches.

**Open questions for owner**
- Is the ML/AI family (about two thirds of candidates) too broad? Narrowing is a config change.
- Add an `extract` job to `daily_ingest.yml` after the backfill is verified? Not done; needs your OK.

**Owner steps**
1. Add `OPENAI_API_KEY=...` to your local `.env` yourself (editor; do not paste it into chat or source the file). Confirm the OpenAI project has the prepaid budget and auto-recharge off.
2. Tell me when it is set. I will run the 20-posting calibration (`jobpulse extract --limit 20 --cap 0.05`, about a cent), show real tokens and cost per posting, and re-project the backfill. Then you approve `extract --batch submit --backfill`.
3. After the backfill: `uv run python eval/sample.py`, copy `eval/labels_template.csv` to `eval/labels.csv`, label it blind (see `eval/README.md`), then `export_predictions.py` and `score.py`.

---

## 2026-11-10: ML/AI family narrowed; 20-posting calibration run

**Filter change (owner request).** `ml_ai_engineer` was two thirds of the candidates and included product managers, designers and "Software Engineer, AI" titles. It is now two rules in `config/extraction.yaml`: (1) `machine learning | ML | MLOps | applied scientist`; (2) "AI" only next to data/analytics/research, minus generic software/backend/frontend/full-stack/infrastructure/platform/product/program/designer/sales/solutions titles. `data scientist` stays its own family. Title rules gained an optional per-rule `exclude` (a global exclude would also have removed "Data Infrastructure Engineer"). I dropped `research scientist`, `deep learning`, `NLP`, `computer vision` and `LLM` because they were not in the list you gave; say if you want any back.

**Counts (`scripts/extraction_counts.py`, silver rebuilt on MotherDuck: 8 models, 54 tests, 0 failures).** 495 of 8,296 active postings pass (6.0%), down from 776. ml_ai_engineer 231 (greenhouse 195, ashby 26, lever 10), data_scientist 94, data_analyst 77, data_engineer 67, analytics_engineer 14, business_analyst 8, bi 4.

**Calibration run (paid, gpt-6-luna, sync, cap $0.05).** 20 postings, `jobpulse extract --limit 20 --cap 0.05`:
- 20/20 valid on the first pass: 0 escalations, 0 quarantined, 0 errors.
- Tokens: input avg 1,865 (1,303-2,547), output avg 271 (133-454), cached 0. Latency avg 3.0 s, max 5.1 s.
- Cost $0.0064 total, **$0.00032 per posting, $0.32 per 1k postings** (from `scripts/report_extraction_cost.py`).
- Sanity look: sponsorship 19 unspecified / 1 not_offered; 13/20 with a salary; mean confidence 0.95. Quality is not judged until the 200 labels exist.
- Total OpenAI spend so far: about $0.006.

**Cost model recalibrated** (`prompt_overhead_tokens` 600 -> 250, `expected_output_tokens` 550 -> 300). `escalation_rate` stays 0.12: 0 of 20 cannot support lowering it.

**Re-projected backfill** (`jobpulse extract --dry-run --backfill`): 475 postings left (20 cached), avg 6,194 chars.
- Batch first pass $0.078 + escalations $0.188 at the assumed 12% = **about $0.27**. Fully synchronous: $0.34.
- Range by escalation rate: 0% -> $0.08, 5% -> $0.15, 12% -> $0.27, 25% -> $0.47. All far below the $3 limit and the $1.50 run cap.
- Batch saves only about $0.08 versus sync, at the price of up to 24 h latency. Sync would finish in roughly 6-8 minutes. Both are cheap; your call.
- Daily incremental at 20-50 new filtered postings: about $0.01-0.02.

**Not done / waiting.** Batch not submitted (per instruction). The `extract` job in `daily_ingest.yml` is not added yet; it goes in after the backfill is verified.

---

## 2026-11-10: Phase 3 backfill done, daily extract job added, blind label sheet created

**Backfill (synchronous, `jobpulse extract --backfill`, cap $1.50).** 475 postings processed (20 were already cached from the calibration): 475 extracted, 11 escalated, 0 quarantined, 0 queued, 0 errors, $0.1866 for the run.

**Verification**
- **Second run makes 0 LLM calls:** `extract --backfill` again reported cache hits 495, processed 0, LLM calls 0, cost $0.0000.
- **`scripts/report_extraction_cost.py` (all runs, 495 postings):** 506 calls (495 gpt-6-luna, 11 gpt-6.1-terra); tokens in/out 921,020 / 138,999; total **$0.1931** (luna $0.1579, terra $0.0352); **cost per 1k postings $0.39**; **escalation rate 2.2%**; **quarantine rate 0.0%**. This replaces the 12% escalation assumption (projected $0.27 for the batch route) with a measured 2.2% (actual $0.19 for everything, calibration included).
- Queue and quarantine tables are both empty. Bronze (8,296) and silver (8,296) counts are unchanged by extraction. `ops.pipeline_runs` has `extract_filter` (8,296 in, 495 kept) and `extract` rows per run, with cost.
- Total OpenAI spend for P3 so far: **$0.1931** of the $4.96 budget.
- Extraction outputs, for orientation only (quality is not judged until the 200 labels exist): sponsorship 482 unspecified / 11 not_offered / 2 offered; 360 of 495 have an advertised salary; mean model confidence 0.956.

**Daily workflow.** `daily_ingest.yml` has a third job, `extract` (`needs: warehouse`, runs only when the warehouse job succeeded, `environment: prod`). It runs `jobpulse extract --cap 0.25`. `OPENAI_API_KEY` and `MOTHERDUCK_TOKEN` are set on that single step only, with no AWS credentials and no dbt. If extraction fails it cannot affect ingest or the warehouse. Postings over the cap go to `extract.queue` and are picked up the next day. The workflow file is parse-checked locally, **not yet run in GitHub Actions**.

**Benchmark sheet.** `uv run python eval/sample.py` drew 200 of the 495 candidates (seed 20261109, 17 strata: title family x source) into `eval/labels_template.csv` and `eval/sample_manifest.json`. The sheet has only posting_key, title, company, description_text and empty label columns (checked: all label cells empty, 200 unique keys). Predictions are only in `eval/predictions.csv`, which does not exist yet and is gitignored.

**Caveat for the sponsorship report.** Only 13 of 495 postings (about 2.6%) have an explicit sponsorship statement according to the model, so a 200-row sample will contain few positive examples. The sponsorship report flags classes with fewer than 5 labeled examples as low support. If you want a real sponsorship benchmark, a second targeted sample of postings that mention "sponsor" or "visa" would be needed (needs your OK, since it changes the stratified-200 definition).

**Open for the owner**
- Run **Daily ingest** by dispatch once to see the `extract` job green on GitHub (needs `OPENAI_API_KEY` in the `prod` environment, which you said is set).
- P3 done-when boxes still open: `score.py` on your 200 labels (needs your labeling).

---

## 2026-11-10: Sponsorship sample, prefix-balanced label order

**Main sample unchanged, order changed.** The same 200 postings (verified: identical `posting_key` set, all label cells empty). The old order was a seeded random shuffle, which was not tight (first 50 rows: 0 analytics_engineer vs 1.5 expected, ml_ai 27 vs 23.2). `eval/sample.py` now uses a seeded **prefix-balanced** order and `sample.py --reorder` re-applied it to the existing sheet (refused if any label exists). After reorder, the first 50 rows are within about 1.5 of the proportional count for every family, and the first 100 within 1.5 (e.g. ml_ai 45 vs 46.5, data_analyst 15 vs 15.5). You can score after 100 rows.

**Targeted sponsorship sample.** `eval/sponsorship_sample.py` -> `eval/sponsorship_labels_template.csv` (blind: id, title, company, description text, one empty column `sponsorship_statement`) and `eval/sponsorship_manifest.json`. Selection is by posting text only (`sponsor|visa|work authori[sz]ation|h-?1b`, in `config/extraction.yaml`), never by model output.
- **Only 23 of the 495 candidates match** (sponsor 20, visa 10, work authorization 9, H-1B 0). 10 of them are already in the main 200 and are excluded to avoid double labeling, so the sample has **13 rows, not the 60 requested**. Between the main sheet and these 13, every in-scope posting that mentions the terms is labeled exactly once.
- In the targeted sheet a blank cell means "not labeled yet": write `offered`, `not_offered` or `unspecified` explicitly.
- Reaching 60 would require extracting postings outside the approved role-family title filter (more LLM spend and a scope change). Not done; needs the owner's call.

**Scoring.** `score.py` includes the targeted labels only in `sponsorship_report.md`: a random-sample section, a targeted-sample section and a pooled view, with a note that the targeted sample is enriched and says nothing about prevalence. It never enters the per-field report. It rejects invalid values (anything other than the three labels) and any posting that is also in the main labels. `export_predictions.py` writes `eval/sponsorship_predictions.csv` (gitignored) next to `predictions.csv`. I did not run it for real, so no predictions file exists yet.

**Checks.** `make check`: ruff, format, mypy strict (36 files), pytest **131 passed**, terraform fmt + validate both roots; gitleaks not installed locally. New tests (6): balanced-order proportionality and determinism; sponsorship sheet is blind, has one label column, is disjoint from main and leaves the main sample untouched; reorder keeps the postings, follows in the manifest, and refuses after labeling; sponsorship predictions go to a separate file; targeted rows feed only the sponsorship report; invalid values and overlap are rejected.

---

## 2026-11-10: Local labeling app

`eval/label_app.py` (Streamlit UI) and `eval/label_io.py` (all logic: CSV I/O, validation, highlighting; no Streamlit, so it is unit-tested directly). Run: `uv run streamlit run eval/label_app.py`.
- Reads/writes only `eval/labels.csv` and `eval/sponsorship_labels.csv`. **Blindness is enforced in code:** the file layer raises `BlindnessError` for any path with "prediction" in its name, and a test asserts the app sources never reference the predictions paths or `export_predictions`.
- One posting at a time: title, company, full description (HTML-escaped first, then highlighted: yellow salary/$, blue remote/hybrid/on-site, pink sponsor/visa/work authorization/H-1B). Enum fields are dropdowns whose options come from the extraction schema; salary and skills are text inputs; **Save & Next** validates, writes `done=y`, and moves to the next unlabeled row.
- Validation before save: salary amounts must be numbers (`$120,000` and `120k` are normalised), min <= max, currency (3 letters) and period required when there is an amount, no stray currency/period without an amount. Skills are normalised to `a; b; c`. In the sponsorship sheet a choice (`offered`/`not_offered`/`unspecified`) is required because blank means not labeled.
- Progress counter in the sidebar, a "first 100 rows labeled" counter, Prev/Next, "Go to next unlabeled", and a jump list (✓ marks labeled rows) to reopen and edit any row.
- Safety: saves re-read the file and change only that row (edits made elsewhere survive), write via temp file + rename, and keep `<file>.bak` of the previous version (gitignored).
- **Dependency added (owner request):** `streamlit` as a new `app` dependency group (1.67.0, already in the planned stack), included in `default-groups` so CI and `make check` can run the app test. The production jobs use `--no-default-groups`, so ingest/warehouse/extract never install it. `uv.lock` changes are additive; duckdb stays 1.5.6. `.streamlit/config.toml` turns off Streamlit telemetry.
- Tests (20, `tests/unit/test_label_app.py`): money/skills parsing, validation rules, save touches only one row and keeps `.bak`, no clobber of edits made elsewhere, progress helpers, HTML escaping and highlighting, predictions files refused, and headless UI runs with Streamlit's `AppTest` (label, save, next, progress, jump back and edit, validation error blocks save, sponsorship sheet requires a choice, missing-files message). I also booted the real server once on a throwaway port (health check ok).
- Your `eval/labels.csv` and `eval/sponsorship_labels.csv` already existed (byte-identical to the templates, 0 rows labeled) and were left untouched and uncommitted.
- Not tested: a real browser session by a human; AppTest drives the same script but not the browser rendering.
- Found and fixed while adding the app: `score.py` demanded sponsorship predictions as soon as `eval/sponsorship_labels.csv` existed, even if it had no labels. It now skips a targeted sheet with no labels, so you can score the main sheet alone (new test). One older test depended on that file not existing and now passes explicit paths. Full check: pytest 152 passed.

---

## 2026-11-11: CI failures #9, #11, #12 investigated; first scheduled Daily ingest recorded

**CI failures (all three are the `gitleaks` job; Python and Terraform jobs passed in every run).** Source: GitHub Actions API (run and job metadata, annotations). The job logs and job summaries need a token, so I could not read the finding list itself. The annotation on each failed run is only "🛑 Leaks detected, see job summary for details".

| Run | Push (commits scanned) | Result |
|---|---|---|
| #9 | `d3be097..8c59a9b` (5 commits: OpenAI extraction code, tests, eval scripts, docs) | failed |
| #10 | `cec921c` | passed |
| #11 | `743e3e9`, `96efa65` (adds `eval/sample_manifest.json` + label CSV) | failed |
| #12 | `6af5a07` (adds `eval/sponsorship_manifest.json`, rewrites the sample manifest) | failed |
| #13 | `950d69a` (labeling app) | passed |

- **Most likely cause (not confirmed from the job summary):** gitleaks' default `generic-api-key` rule matching the JSON key `"posting_key"` (contains the word "key") followed by a 64-character hex value (sha256, high entropy). These are posting identity hashes of public job postings, **not secrets**. A local re-implementation of the rule over each push's added lines flags exactly the failing pushes and none of the passing ones: 200 hits in `eval/sample_manifest.json` (= the 200 posting keys) in #11, 190 + 13 hits in the two manifests in #12, 0 in #10 and #13. For #9 it only finds two weak matches (a fake `sk-abc12345678` used in a unit test and one code line), so the exact #9 finding is the least certain.
- **No real secret found.** Pattern scan of all 14,903 added lines in the full history: 0 OpenAI-key, AWS-key, GitHub-token, private-key or JWT (MotherDuck token format) matches. `.env`, `*.pem` and `*.tfvars` were never tracked. This is my own approximation, not a gitleaks run; gitleaks is not installed locally and the sandbox declined a download of the binary.
- **Is it fixed? No.** #13 is green only because gitleaks-action scans just the commits in each push, and #13's push did not contain the manifests. The offending commits are still in history, there is no `.gitleaks.toml`/allowlist, and the manifest files are still in the repo. The same failure returns the next time a push touches those files (for example a `sample.py --reorder` or redraw), and a full-history `gitleaks detect` (what `make secrets` runs once gitleaks is installed) would fail now.
- **Proposed fix (not applied; it changes a security control, so it needs the owner's OK):** add `.gitleaks.toml` with `[extend] useDefault = true` and a narrow path allowlist for `^eval/(sample|sponsorship)_manifest\.json$` and the unit test that contains fake keys. Path allowlists apply to history too. To confirm the cause first: open the job summary of run #11's gitleaks job (https://github.com/akshaygovindd/jobpulse/actions/runs/42745258967/job/129595702988) and check that the rule is `generic-api-key` on `eval/sample_manifest.json`. After the fix, verify with `gitleaks detect --source . --redact` locally.

**First scheduled Daily ingest: run #7 (attempt `gha-42861116567-1`).** Triggered by `schedule` on 2026-11-10 at 12:04 PM EST (17:04 UTC) for snapshot_date 2026-11-10. The cron is 09:17 UTC, so GitHub started it about 7 h 47 min late. Commit `950d69a`. All three jobs succeeded: `ingest` 27 s, `warehouse` 56 s, `extract` 18 s.
- Ingest: ashby 956, greenhouse 6,899, lever 417 = 8,272 postings. Silver 8,272. Title filter: 489 of 8,272 pass.
- Extract: 6 new postings, 6 calls, $0.0018, 0 escalations, 0 quarantined, 0 queued. This is the first scheduled `extract` run, and the second Actions run of that job after dispatch #6 (attempt `gha-42746243545-1`: 2 postings, $0.0006). It confirms `OPENAI_API_KEY` works as a `prod` secret in Actions.
- Warehouse totals: extractions 503, quarantine 0, queue 0. Total OpenAI spend so far $0.1954 of $4.96 (calibration $0.0064, backfill $0.1866, the two Actions runs $0.0024).
- Side effect worth knowing: the scheduled run is a later complete attempt for the **same** snapshot date (2026-11-10, UTC) as the earlier dispatches, so it replaced that date's pointers (8,298 postings -> 8,272). Postings an employer removed in between simply no longer appear, and there is no closure yet because closure needs two consecutive dates.

**P1 tracking check (two consecutive successful scheduled runs):** day 1 recorded, 2026-11-10 (run #7). Day 2 is open. As of 2026-11-11 16:25 UTC (11:25 AM EST), no Nov 11 run has appeared in the Actions API; with the Nov 10 delay of about 8 h the 09:17 UTC schedule would start around 17:00 UTC. The owner will confirm; the box stays unchecked until then.

---

## 2026-11-11: Manual labeling stopped; pilot error analysis and automated evaluation

**Owner decision:** manual labeling stops at **38 rows**, kept as the hand-labeled pilot (`eval/labels.csv`, left untouched and untracked). Further evaluation needs no human labels and is reported separately, each named for what it measures. None of it is an accuracy figure. ROADMAP P3 done-when and SPEC were rewritten to say so; the original "200 owner labels" check is **not met** and is recorded as dropped, not ticked.

**Prompt decisions (owner answers).** Seniority: keep the 9 levels and map at scoring time. Skills: keep the tools-only prompt and normalize at scoring time. So the **prompt and schema are unchanged** (prompt version `2fd688afd45c`), no cache invalidation and no re-extraction cost. The mappings live in `config/extraction.yaml` (`eval:`), are always reported next to the raw numbers, and never alter stored extractions.

**Hand-labeled pilot (n=38; `eval/score.py`, `eval/pilot_analysis.py`).**
- Seniority: raw exact match 21/38 (precision 55.3%, recall 56.8%). With staff/principal/manager/director/executive folded into senior: **31/38 (precision 81.6%, recall 83.8%)**. Correction to what I told you while asking: I estimated 32/38; folding resolves 10 of the 17 disagreements, not 11 (I wrongly counted "Manager, Site Analytics", which you labeled mid). The 7 left are not a staff/manager convention: three "Sr." titles with 4-5 years labeled `mid`, "Sr. Product Manager" `mid`, "Manager, Site Analytics" `mid`, "Business Intelligence Analyst" `mid` vs model `junior` (you never use `junior`), "Data Platform Engineer" `senior` vs model `mid`, and one "Senior Machine Learning Engineer" labeled `unspecified` (possible label slip; worth a look).
- Skills: you listed 30.2 per posting, the model 18.4. Raw precision 71.3% / recall 43.6%; name-normalized 74.8% / 45.7%; normalized with competencies excluded 74.6% / 48.8%. Normalization barely helps, so the gap is **scope, not spelling**: of 623 missed labels only 29% appear verbatim in the text, 24% are phrases of 3+ words (model evaluation, performance optimization, production ML systems), and only 13% are the soft-skill competencies the tools-only pattern excludes. Keeping the tools-only prompt therefore leaves skills recall near 49% against your labels, by design.
- Other fields (not requested, shown in `eval/reports/extraction_report.md`): role_family P 71.1 / R 75.0, remote_policy P 76.9 / R 87.0, salary amounts 96.4/96.4.
- **Separate defect found:** in 6 of 28 pilot postings where the model returned a salary amount it left `salary_period` empty (amount right, period missing: e.g. 123,200 with no `year`). Validation does not require a period or currency when an amount exists. Not changed (a validation rule would alter escalation and cost); proposed for the owner to decide.
- Sponsorship in the pilot: 1 `not_offered`, 0 `offered`, 37 `unspecified`, all correct, so it says almost nothing. The 13-row targeted sheet is unlabeled, so **sponsorship accuracy remains effectively unmeasured**.

**Automated evaluation (no human labels), run on the live warehouse, current prompt version, 503 stored extractions (489 with posting text still available).**
1. *Rule-based salary cross-check* (`eval/salary_crosscheck.py`, agreement not accuracy). Parser found a pay range in 67.7%, model returned a salary in 72.4%. Where both found one, the model's amounts equal a parsed range in **96.4% (319/331)**, 16 of those being an "overall envelope" answer across location tiers. 7 partial, 5 mismatch, 0 where the parser found a range and the model returned nothing, 23 where only the model found one (unverifiable), 135 where neither did. **Caveat on the headline:** I refined the parser once after seeing its disagreements (envelope class, per-period sanity bounds); the first run agreed 91.6%. I hand-checked 3 of the 5 mismatches: all three were **parser errors**, not model errors (two European-format amounts like "€56.000-€79.000" read as 56.0 per hour, and one "New hire equity: $48,000-$72,000" read as pay). That hand check is not scripted. I stopped tuning rather than fit the parser to the model further.
2. *Validation and grounding pass rates* (`eval/grounding_report.py`, self-consistency). First-pass model passed validation without escalation for **97.8% (492/503)** of calls (structural and grounding failures cannot be separated; the pipeline does not record which); 503/503 postings accepted, 0 quarantined; stored salary quotes found in the posting text **354/354**, sponsorship quotes **12/12** (this holds by construction, since an extraction is only stored after the check; it guards against later data changes, and says nothing about whether a value is right).
3. *Cross-model agreement* (`eval/agreement.py`, consistency not accuracy): **built and tested, not run.** Seeded sample of 100 (seed 20261111) from postings the first-pass model answered validly, so the few hardest postings (the 2.2% that needed escalation) are excluded and agreement will likely read high. `gpt-6.1-terra` is called one posting at a time with the same prompt; results go to `extract.agreement` and `llm_calls` rows with `mode='eval'` (excluded from cost per 1k). **Cost estimate (dry-run, no API call): about $0.33 from the config token model, about $0.32 from the 11 terra calls already observed ($0.0032/call).** Hard cap $0.40, counted across restarts; worst-case reservation per call is $0.0165, so the run can stop a few postings short only if actual cost runs about 15% over the estimate. Batch (half price) is not implemented for this path.

**Other changes:** `quote_in_text` public helper in `schema.py`; `agreement` table in `Store`; `report_extraction_cost.py` now excludes `mode='eval'` calls from its figures; shared test fixtures moved to `tests/unit/conftest.py`; reports no longer embed absolute local paths.

**Checks:** see the final entry below for the full `make check` result.
