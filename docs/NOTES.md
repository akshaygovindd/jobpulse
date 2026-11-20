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

---

## 2026-11-11 (later): agreement run, deterministic salary_period, gitleaks allowlist, pilot labels committed

**1. Cross-model agreement run (`eval/agreement.py --run`, paid, approved, cap $0.40).** `gpt-6.1-terra` re-ran the same prompt on the seeded 100 (seed 20261111) postings whose stored first-pass output came from `gpt-6-luna`. **100/100 valid, 0 API errors, cost $0.2687** (estimate was $0.33; terra averaged 1,821 input / 173 output tokens and 3.2 s per call, so it was cheaper than the config assumed). Consistency, **not accuracy**; the sample excludes the ~2% the first pass failed on, so agreement likely reads high.

| Field | Agree (all 100) | Agree (>= 1 model gave a value) |
|---|---|---|
| role_family | 96.0% | 96.0% (96/100) |
| remote_policy | 93.0% | 89.6% (60/67) |
| seniority, exact | 88.0% | 87.5% (84/96) |
| seniority, staff/principal/manager/director/executive = senior | 89.0% | 88.5% (85/96) |
| salary_min / max / currency | 100% | 100% (69/69, 69/69, 68/68) |
| sponsorship_statement | 100% | 100% (4/4, so almost no information) |
| salary_period | 66.0% | 35.8% (19/53) |
| skills | mean Jaccard 65.7% | luna's skills also in terra's list 75.3%; terra's also in luna's 83.1%; luna lists 17.9 per posting, terra 16.3 |

- **I got the `salary_period` explanation wrong at first and corrected it after checking.** I had said the cheaper model omits the period more than terra. The data say the opposite: in **34** postings luna said `year` and **terra left the period empty**; there is no case of luna empty and terra filled. Both models leave the period out at times, terra more often, and validation accepts it. That is why the deterministic fill (below) is the right fix for the pipeline regardless of which model answers.
- Seniority: 10 of the 12 exact-match disagreements are terra saying `unspecified` where luna inferred a level (mid 5, senior 3, junior 2); the rest are staff/senior and mid/senior. So the two models differ mainly in how willing they are to infer a level the posting does not state, which is the same ambiguity behind the owner's years-of-experience labeling (the pilot's notes column cites required years in most rows; a "years -> level" prompt rule is a candidate for a future prompt version and was not applied).

**2. Deterministic `salary_period` fill (owner decision; no API calls, no re-extraction).** Rule in `src/jobpulse/extract/period.py`, thresholds in `config/extraction.yaml` (`salary_period_inference`): when an amount exists and the period is empty, `year` if the amount (salary_min, else salary_max) is >= 20,000, `hour` if 0 < amount < 500, otherwise left empty; a period the model gave is never changed. `extract.extractions` gained `salary_period_inferred` (BOOLEAN). The rule runs when an extraction is stored and, idempotently, on rows already stored (`Store.__init__`, so every consumer sees migrated data).
- Live data before: **74 of 369** extractions with an amount had no period (20%). Applied: **72 -> year, 1 -> hour** ("$170.40-$255.60"), **1 left empty** ("EUR 5.000-6.250", a monthly-looking amount, correctly not guessed). 295 model-supplied periods untouched. Counts above are from the migration run; the table now holds 514 extractions (11 more came from the Nov 11 scheduled run).
- Reports judging the model ignore inferred values: `salary_crosscheck`, the agreement report, and the pilot's `salary_period (model output only)` row. The pilot's headline `salary_period` row is the pipeline output. **Pilot salary_period: pipeline output P 96.4% / R 96.4% (27/1/1); model output only P 95.5% / R 75.0% (21/1/7).** The recall gain is a rule, not model quality, and is labeled that way.
- Operational note: I deployed the code **before** touching the live table, because the previous deployed code inserted positionally and would have failed once the column existed (the Nov 11 scheduled run had already finished, nothing was in flight). The insert is now by column name. Tests: rule boundaries (20,000 / 500), legacy-table migration, idempotency, model period never touched, scoring split.

**3. gitleaks allowlist (`.gitleaks.toml`).** Keeps all default rules (`useDefault = true`) and allowlists exactly `^eval/(sample|sponsorship)_manifest\.json$` (public posting sha256 hashes behind a JSON key named `posting_key`) and `^tests/unit/test_extraction.py$` (obviously fake keys). Trade-off: a path allowlist hides everything in those files, so a real key pasted into that test file would not be caught. CI #15 (the push with the config) is green, **but that push did not touch the manifests, so it does not prove the allowlist works.** I could not run gitleaks here. Proof needs `gitleaks detect --source . --redact` (full history) on a machine with gitleaks installed, or the next push that modifies a manifest.

**4. Pilot labels committed.** `eval/labels.csv` (200 rows, 38 labeled, `notes` column filled for those 38) is now tracked, as the owner asked. It contains posting text from public job pages (including a few recruiting email addresses that are in the postings themselves) and the owner's labels. `eval/sponsorship_labels.csv` stays untracked (unlabeled; the owner will label it later). Predictions files stay gitignored.

**5. Scheduled-run check closed.** Daily ingest #7 (Nov 10, 17:04 UTC) and #8 (Nov 11, 17:09 UTC) were both `schedule` events and fully green (ingest, warehouse, extract). Run #8: 8,481 postings, 499 pass the title filter, 11 new extractions for $0.0033, two authoritative snapshot dates (Nov 10 and Nov 11, 43 pointers each), 0 closures (closure needs absence on two consecutive dates, so the first can appear after Nov 12). ROADMAP P1's tracked box is ticked. GitHub's cron lag is about 8 h; the actual run times are about 12 PM EST.

**6. Numbers refreshed.** Because the table grew to 514 extractions and postings changed, the automated reports now cover 514 extractions (499 with text still available), so figures differ slightly from the previous entry: salary cross-check 96.1% (323/336) agreement where both found a salary; first-pass validity 97.9% (503/514); 0 quarantined. Total OpenAI spend to date **$0.4675** of $4.96 (extraction $0.1988, evaluation $0.2687; the evaluation calls are excluded from cost per 1k postings, which stays about $0.39).

**P3 status:** all done-when boxes ticked under the revised scope (see ROADMAP), with the gaps recorded: sponsorship accuracy unmeasured, skills recall near 49% by design, n=38.

**Open for the owner:** (a) label `eval/sponsorship_labels.csv` (13 rows) when ready; (b) decide whether to add a "years of experience -> level" rule in a future prompt version (would invalidate the cache and cost about $0.20 to re-extract); (c) install gitleaks locally (`brew install gitleaks`) and run `make check` to prove the allowlist against full history.

---

## 2026-11-11 (evening): gitleaks allowlist verified on full history

The owner ran `make check` locally with **gitleaks 8.30.1** against commit `e4cb080` (reported by the owner; I did not run it, since gitleaks is not installed in my environment): 198 tests passed, both Terraform roots valid, and gitleaks scanned **32 commits (the whole history: `git rev-list --count HEAD` is 32) and found no leaks**. That full-history scan includes the two commits that earlier made CI #11 and #12 fail (`96efa65`, `6af5a07`, which add the benchmark manifests) and the unit-test commit with the fake keys, so with `.gitleaks.toml` in place the history is clean. This closes the verification gap noted in the previous entry (item 3), which had only a green push that did not touch the manifests.

What this does and does not show: it shows the repository, as configured, has no findings. It does not isolate which findings the allowlist suppressed. A scan with the config bypassed (for example `gitleaks detect --source . --redact --config <a copy of the default config without the allowlist>`) would list the suppressed findings and confirm they are exactly the manifest hashes and the test keys. Optional; not done. The allowlist's trade-off stands: everything inside `eval/*_manifest.json` and `tests/unit/test_extraction.py` is exempt from scanning, so no real credential may ever be put in those files.

---

## 2026-11-11 (afternoon): P4 part A, dbt gold + snap_postings + daily `gold` job

**Plan approved by the owner, then built in four small commits.** Part B (local Airflow in Docker Compose) is **not started**: it waits for Docker Desktop. Nothing in part A has run against live MotherDuck yet (see "What is not proven").

**What changed and why**
- **Gold layer (dbt, schema `gold`)**: `fct_postings`, `dim_company`, `dim_skill`, `bridge_posting_skill`, `agg_skill_demand_weekly`, `agg_salary_by_role_city`, and the snapshot `snap_postings`, plus `int_latest_extraction` in a separate `gold_stage` schema. Every model has grain and not-null tests; enum columns have `accepted_values`; `bridge_posting_skill.skill_id` has a `relationships` test. Column and grain details are in ARCHITECTURE.md ("gold").
- **Order in the daily workflow (decision, documented in ARCHITECTURE.md and the workflow comments):** a fourth job `gold` after `extract`, running when `warehouse` succeeded **even if `extract` failed or hit its cap**. Extractions are a persistent cache, so gold then uses what is already stored (at worst yesterday's). New or changed postings show `has_extraction = false` until the next extract. Running gold before extract was rejected because it would always lag a day.
- **Silver is now scoped** to staging + silver models (`--select`), so `build-silver` no longer needs the `extract` schema. A new `jobpulse build-gold [--db]` stages two mapping tables in Python, then runs `dbt build` on gold + snapshots, and records a `task='gold'` row in `ops.pipeline_runs`.
- **Skill names:** `norm_skill` moved from `eval/score.py` to `src/jobpulse/skills.py` (score.py imports it; eval behavior is unchanged). `dim_skill` and the bridge use it through `gold_stage.skill_map`.
- **Config:** `warehouse.gold_schema`, `gold.annualization` (year 1, month 12, week 52, hour 2080; `day` is deliberately not annualized) and `gold.min_salaries_for_percentiles` (5) in `config/pipeline.yaml`; city rules in `config/locations.yaml`.
- **`scripts/gold_report.py`**: read-only; the source for every gold figure quoted in docs (counts, city share, salary coverage, snapshot rows, database size).

**Decisions that deviate from the earlier ARCHITECTURE table (you approved the plan; flagged again so they are visible)**
1. Not built until P5: `sponsorship_class`, `country`, `dim_company.lca_match_confidence`/`visa_evidence_class` (they need the LCA loader and employer match). `fct_postings` keeps only the extracted `sponsorship_statement`.
2. `city` is a heuristic on `location_raw` (no structured location exists). On the live data (8,481 active postings, read-only check before writing the rules) **62.3% get a city**; the rest are remote-only, country-only or multi-location strings, which stay NULL on purpose. I tuned the rules against that data (for example "New York, NY" had wrongly returned NULL because New York is also a state), so the 62.3% is a development figure, not an independent measurement. Known flaw: same-named cities in different countries merge (Dublin, CA and Dublin, Ireland, which together are about 230 active postings). Fixing it needs a country/state field, which is new scope.
3. `dim_skill.category` omitted (no source for categories).
4. `agg_salary_by_role_city` has `salary_currency` in the grain so currencies are never blended. Consequence for P5: `n_postings`, `n_with_salary` and `coverage_pct` describe the whole group and repeat on each currency row, so they must not be summed across rows (documented in the model and ARCHITECTURE.md).
5. Cross-source probable duplicates are excluded from both aggregates so one job is not counted twice. In the first week of data every posting counts as `new_postings` (history starts then), so the first week reads as a baseline.

**MotherDuck storage impact:** not measured yet. The plan's estimate (under about 3 MB, against 54.7 MiB at the Nov 10 reading) is an estimate. Gold tables are rebuilt each run (replace, no growth) except `snap_postings` (one row per posting, then one per change; no description text). The first live `build-gold` will print `database_size` through `scripts/gold_report.py`; run it before and after and I will record the real numbers.

**Checks run (local, `make check`):** ruff check, ruff format --check, mypy (40 source files) all clean; **235 passed** (was 198 at the last entry; +28 city/skill tests, +9 gold tests); both Terraform roots valid; gitleaks 33 commits, no leaks. The gold tests (`tests/unit/test_gold.py`) build silver + gold on a local DuckDB file from synthetic postings and hand-written extractions: 53 gold dbt tests pass in each build, annualization (year/hour/month; day and missing period stay NULL), per-currency separation, coverage math, percentiles NULL below the minimum, skill-name collapsing, weekly demand counts, idempotent rerun, snapshot versioning on a content change, latest extraction wins, gold on an empty `extract.extractions`, and the CLI run record. **CI for the push (`6a77ec0`) was not checked by me** (no `gh` here and the unauthenticated GitHub API is rate-limited), so the CI result is unverified until the owner looks at the Actions tab.

**What is not proven**
- No live run: the gold job has not run in GitHub Actions and `build-gold` has not run against MotherDuck. The workflow file is parse-checked only. A first live run could still hit a MotherDuck-specific difference (for example snapshot DDL).
- The fixture tests use synthetic extractions; real extractions have messier skills and locations than my tests.
- ROADMAP P4 boxes stay unticked until the live build and the Airflow demo exist (the fixture-CI box is ticked only once the owner confirms CI is green on `6a77ec0`).

**Open questions for the owner**
- Run the first live gold build (manual steps in the session reply) and send me the `scripts/gold_report.py` output so I can record real counts and storage.
- Docker Desktop: when it is installed, run `docker --version`, `docker compose version` and `docker run --rm hello-world`, and tell me; then I build part B.
- Part B will need a dev S3 prefix setting (the CLI has none today): OK to add it as a config value?


---

## 2026-11-19: P4 continued (places, local Airflow demo); MotherDuck live run blocked

**Blocker: MotherDuck access.** When I connected to reproduce the failed live `build-gold`, MotherDuck refused the connection before any gold code ran: `Your MotherDuck trial has ended … An organization admin must select from Free, Lite, or Business plans to restore access.` So item 1 is **not done**: I could not reproduce the `CatalogException`, so I wrote no fix and no regression test for it (a test for an error I have not seen would be a guess). The live `database_size`/counts are not recorded, and the live city share is not measured on MotherDuck.
- What I did instead: `build-gold` now prints the first line of the scrubbed error text (token redacted) instead of only the exception class, so the next live failure names the real cause. I also loaded the real S3 snapshots (read-only S3, 430 pointers, 82,849 posting rows over several days) into a **local** DuckDB file and ran silver + gold there: 61 dbt nodes passed, 0 errors. That agrees with your report that local passes and MotherDuck fails; it does not tell us why MotherDuck fails.
- `scripts/gold_report.py` no longer crashes when the gold schema does not exist (prints "does not exist yet: run build-gold first" and still shows `database_size`) and tolerates a missing table; two tests cover it (empty warehouse; built gold).

**City fix (item 2).** A place is now (city, state/province, country), so `Dublin, California` and `Dublin, Ireland` never merge. Rules and word lists are in `config/locations.yaml` (`us_states`, `ca_provinces`, `countries`, `country_codes`, `known_places`). `fct_postings` has `city` (display label, unique per place), `city_name`, `city_state`, `city_country`; `agg_salary_by_role_city` keeps its grain and now splits by place.
- Real-data findings that shaped it: prefixed forms like `GB-London`, `JP-Tokyo`, `CA-Ontario-Toronto` had been producing junk "cities" (now parsed); `Toronto, CA` meant Canada, not California (fixed for cities known to be Canadian); `Redwood CIty` (typo) and `Redwood City` produced two labels for one place (the more common spelling now wins).
- Bare names: `known_places` fills in a state or country only for the cities listed there (London, Toronto, Singapore, Boston and similar). **Bare `Dublin` (101 active postings, mostly Stripe and MongoDB) is deliberately not listed**, because Dublin also exists in California (`US-CA-Dublin`, `Dublin, CA`), so it stays its own place and does not merge with `Dublin, Ireland` (147). Say if you want it mapped to Ireland.
- Parse share, same local data (8,464 active postings, load of 2026-11-19): **62.5% before, 62.4% after** (`scripts/gold_report.py`). So the change is about correctness, not coverage. The remaining NULLs are remote-only, country-only or multi-location strings, by design. The Docker run's data (a later fetch, 8,261 active postings) read 62.5%. **Not measured on MotherDuck.** After the change: 408 distinct places, 0 place keys with two labels. Dublin splits as Dublin, Ireland 147 / Dublin (bare) 90 / Dublin, California 11.
- Tests: 51 → 56 parser tests (labels, prefix forms, no-merge cases, config consistency) plus one gold-level test through dbt (Dublin x3, Redwood spelling variants).

**Part B: local Airflow demo (item 3).** Directory `airflow/`: `Dockerfile`, `docker-compose.yml`, `dags/jobpulse_demo.py`, `README.md` (exact commands).
- **Isolation (owner decision: local storage, no credentials at all).** The container has no AWS, MotherDuck or OpenAI variables and no `env_file`; the image build uses an allow-list so `.env` is never copied (I checked inside the image: no `.env`, only `AWS_EC2_METADATA_DISABLED` and `JOBPULSE_ENV=dev`). Storage is a local directory and the warehouse a local DuckDB file, both in the gitignored `airflow/demo-data/`. Port 8080 binds to 127.0.0.1.
- **CLI dev mode (kept as cheap safety).** New global `--env dev` (or `JOBPULSE_ENV=dev`): requires a local `--db` (a `md:` path or URL is refused; the MotherDuck token is never read), and if S3 is ever used, wraps it so every key lives under `storage.dev_prefix` (`dev/`, new config value). `storage.prod_prefixes` (`raw/`, `snapshots/`, `ops/`) is the prod-write guard: the dev prefix must be non-empty, end in `/`, and neither sit inside nor above those roots. The DAG's first task also fails the run if any AWS, MotherDuck or bucket variable is present. Production behaviour is unchanged (`--env prod` is the default).
- **Extract** is a dry run unless the DAG is triggered with `real_extract: true`, and the key only enters the container through the opt-in `docker-compose.extract.yml` + a gitignored `airflow/openai.env`. **The real-extract path is untested** (no paid call was made; I only checked that the override file renders).
- **Verified end to end, from a clean state:** `docker compose up`, trigger `jobpulse_demo`: all 8 tasks (check_isolation, ingest, load_ops, load_bronze, build_silver, extract, build_gold, gold_report) succeeded in about a minute. Live public job boards: 8,261 postings, 43 companies, silver and gold built, 53 gold tests passed, `snap_postings` 8,261, `extract` dry run (508 pass the title filter, projected $0.37 sync, 0 spent). Local DB 253.5 MiB. Inside the container: `build-gold --db md:jobpulse` exits 2 (refused), `build-silver` without `--db` exits 2, and the guard task refuses when `MOTHERDUCK_TOKEN` is set.
- Not proven: S3 in dev mode (only unit-tested with a fake S3 client), the Docker run in CI (it is manual by design), and anything about MotherDuck. Airflow is standalone/SQLite/sequential: a demo, not a production setup. Docker Desktop was not running; I started it with `open -a Docker`.
- New tests: `tests/unit/test_env.py` (prefix guard, prefixed storage, dev refusals through the CLI) and `tests/unit/test_airflow_demo.py` (static checks that compose has no credentials/env_file, the build context excludes `.env`, the DAG uses `--env dev` with local storage).

**ROADMAP P4:** ticked: dbt build in CI (CI #18 on `6a77ec0`, #19 on `76b1490`, confirmed green by the owner), Airflow DAG end to end against the dev target, both environments documented. **Left unticked:** "All gold models and snap_postings build" because it has not built on MotherDuck.

**Checks run (`make check`, final):** ruff check and format clean; mypy strict, 41 source files; pytest **303 passed** (3 live deselected; was 266 before Part B); terraform fmt + validate both roots valid; gitleaks 38 commits scanned before the new commits, no leaks. Skipped: `pytest -m live` (no network tests needed for these changes); the MotherDuck run (blocked, see above); the Docker run is manual and was done by hand as described.

**Open questions / owner steps**
1. **MotherDuck:** the trial has ended. An organization admin must pick Free, Lite or Business at app.motherduck.com. Until then daily `warehouse`, `extract` and `gold` jobs cannot work either (check the Actions tab). Note that DuckDB is pinned to 1.5.6 for MotherDuck. After access is restored, run `uv run jobpulse build-gold` and send me the full error line (it is now printed); I will reproduce, fix and add the regression test, then record `database_size` and counts.
2. Map bare `Dublin` to Ireland? (config line in `known_places`.)
3. Try the demo: commands are in `airflow/README.md`.

---

## 2026-11-19 (later): bronze storage mitigation, Dublin, Actions history; MotherDuck still refused from this machine

**1. Live `build-gold` and the `CatalogException`: not reproduced.**
- With the token in `.env`, `jobpulse build-gold` (and a read-only `scripts/storage_projection.py`) still fail at connect with the same `Your MotherDuck trial has ended` error, tried several times over a few minutes after you restored the plan. So the `.env` token may belong to the old org/trial, or the plan change has not reached it. **Please generate a new token in the restored org and put it in `.env` and the GitHub `prod` secret `MOTHERDUCK_TOKEN`** (editor only, not the chat). Until then I cannot run anything live.
- **New evidence from the Actions API:** the `gold` job was **green on MotherDuck in Daily ingest runs #9 (dispatch, 2026-11-11) and #10 to #14 (scheduled, 11-12 to 11-16)**, all on commit `76b1490`. So gold does build on MotherDuck from CI, and the `CatalogException` you saw must be specific to that local run. Hypothesis, **not verified**: your manual run overlapped CI run #9, whose gold job ran 18:44 to 18:51 UTC on 2026-11-11, and two sessions doing DDL (`CREATE SCHEMA`, `DROP`/`CREATE TABLE`) in the same MotherDuck database at once can conflict; that would also explain `gold_report.py` finding no gold schema at that moment. I wrote no regression test because I cannot reproduce it; a test for a guessed cause would prove nothing. If it happens again, `build-gold` now prints the first line of the error.
- **Speed/cost fix I could verify locally:** the gold jobs took 2 to 7 minutes in Actions (runs #9, #12, #14: 7 min) against seconds locally. The cause is almost certainly `executemany` in `stage_maps` (one MotherDuck round trip per row, about 1,500 location rows plus skills). It now inserts 500 rows per statement (`gold.bulk_insert`, unit-tested with 1,203 rows, empty input, NULL and unicode). Why it matters: the Lite plan has 10 CU-hours a month. **Expected effect on MotherDuck is unmeasured.**
- ROADMAP P4's last box is **ticked on the strength of the Actions evidence** (gold job green on MotherDuck for six consecutive days). That evidence is job and step conclusions, not logs, and I have not re-run it myself. Say if you want it unticked until you have run it.

**2. Bare `Dublin` -> Ireland** (`known_places`, owner decision). On the real local data: Dublin, Ireland 237 active postings (the explicit 147 plus the bare 90), Dublin, California 11. `Dublin, CA` and `US-CA-Dublin` keep their explicit state. Tests updated (parser, gold).

**3. Storage mitigation (implemented).** Bronze now keeps the description text and raw payload **once per (posting, content_hash)** in new `bronze.posting_content`; `bronze.raw_postings` keeps only the small per-day observation fields. This is the "keep payloads only when content_hash changes" idea, done as content-addressing so it is order-independent and idempotent (a same-day replacement or an A→B→A change cannot lose content). `stg_raw_postings` joins the description back, so silver and gold are unchanged (all 59 silver and 53 gold dbt tests pass on the migrated real data, and every one of 9,516 `dim_postings` rows still has its description). S3 raw pages are untouched and remain the complete record, so payload history can always be re-read from S3.
- **Migration** (`migrate_to_content_table`, runs inside `load-bronze`, idempotent, one transaction): builds `posting_content` from the earliest observation of each content, rebuilds `raw_postings` without the two large columns (a rebuild, not `DROP COLUMN`, so space is released). **It has not run on MotherDuck.** It is the one unverified step of the next warehouse job: if it fails, the transaction rolls back (old data intact) but `load-bronze` fails with it. I would run `uv run jobpulse load-bronze` locally once, with the new token, before the next scheduled run.
- **Measured on the real 10-day dataset** (82,849 observation rows, 2026-11-10 to 2026-11-19, local DuckDB, `scripts/storage_projection.py`): used size **1,541.8 MiB -> 320.2 MiB (-79%)**. Content rows 10,568 against 82,849 observations (87% of daily rows were exact repeats before).
- **Projected growth** (`--marginal 3`: cost of the last three days, rebuilding silver and gold each time): 3.75, 4.25 and 2.50 MiB per day, **mean 3.5 MiB/day, about 105 MiB per 30 days = 1.0% of the 10 GiB limit**. Before the change the same 10 days averaged about 154 MiB/day (1,541.8 MiB / 10 days, including the first full day), which would have been roughly 4.5 GiB a month and about 45% of the limit. **Caveats:** this is local DuckDB "used blocks", and your MotherDuck reading on 2026-11-10 (54.7 MiB for one day) was well below the local figure for a comparable day, so MotherDuck's number may differ; this says nothing about CU-hours, which the 10 CU-hour limit also counts; and it assumes today's volume and change rate continue. I will re-measure on MotherDuck after a week (`uv run python scripts/storage_projection.py`).
- Tests (`tests/unit/test_bronze_content.py`, 6): unchanged postings store content once; A→B→A keeps two contents and three silver versions; payload kept from the first observation; a replaced same-day attempt leaves no orphan; migration from the old shape is lossless and idempotent; rerun loads nothing.
- `scripts/warehouse_counts.py` now also counts `bronze.posting_content`.

**4. Actions history (GitHub API, unauthenticated; job and step conclusions only, I cannot read logs).**

| Run | Date (UTC) | ingest | warehouse | extract | gold |
|---|---|---|---|---|---|
| #9 dispatch | 11-11 | ok | ok | ok | ok |
| #10 to #14 | 11-12 to 11-16 | ok | ok | ok | ok (2 to 7 min) |
| #15 | 11-17 | ok | **failed at "Load ops JSON into MotherDuck"** | skipped | skipped |
| #16 | 11-18 | ok | **failed (same step)** | skipped | skipped |
| #17 | 11-19 | ok | **failed (same step)** | skipped | skipped |

- Matches the trial ending between 11-16 and 11-17. Ingest was unaffected: S3 has **43 of 43 authoritative pointers for each of 11-15 to 11-19**, and the 11-19 run report shows 43/43 complete, 8,267 records.
- **No day needs a manual re-run.** `load-bronze` lists every pointer in S3 and loads any date whose attempt is not yet in the warehouse, `load-ops` reads all ops JSON, silver closure is computed from the pointer dates, and `extract` works on whatever is pending (the three missed days of new postings are small against the $0.25 cap, and anything over the cap is queued). The next scheduled run (09:17 UTC, in practice about 17:00 UTC) catches everything up, **once MotherDuck access works from the `prod` secret**. Two minor side effects: `snap_postings` records changes seen during the outage with the catch-up date as their valid-from, and the catch-up run is the first to run the bronze migration (see above). Note the Actions history can't tell me whether the `prod` secret token works now; I could not see a run after the restore.

**Checks run (`make check`, final):** ruff check and format clean; mypy strict 41 files; pytest **310 passed** (3 live deselected; was 303); terraform fmt + validate both roots valid; gitleaks no leaks. Not run: anything against MotherDuck (access refused), and `pytest -m live`.

**Owner steps**
1. New MotherDuck token from the restored org -> local `.env` and GitHub `prod` secret `MOTHERDUCK_TOKEN`.
2. Locally, once: `uv run jobpulse load-bronze` (runs the migration; watch for errors), then `uv run jobpulse build-silver`, `uv run jobpulse build-gold`, and `uv run python scripts/gold_report.py` and `uv run python scripts/storage_projection.py`. Send me the output (or any error line) and I will record the live numbers.
3. Then dispatch **Daily ingest** once (or wait for the schedule) and check that all four jobs are green.

---

## 2026-11-19 (night): live MotherDuck run recorded; P4 complete

**Live run (owner, from the local machine, new token, `JOBPULSE_BUCKET` exported):**
- `load-bronze`: **129 pointers loaded, 24,753 postings, 0 failed** (the 2026-11-17 to 11-19 outage catch-up; this was also the first run of the bronze migration to `bronze.posting_content`, which therefore worked on MotherDuck).
- `build-silver`: **67/67** (models plus tests). `build-gold`: **61/61**.
- Gold figures (`scripts/gold_report.py`, run by me afterwards, read-only): `fct_postings` **9,516** (8,464 active); **481 with extraction** (the outage-day postings are not extracted yet; the next scheduled `extract` picks them up), 441 active, non-duplicate and extracted; parsed city **62.4%**; extracted with advertised salary 73.2%; 1 row with a salary but no annualizable period; 65 salary periods inferred; `dim_company` 43; `dim_skill` 2,484; `bridge_posting_skill` 8,262; `agg_skill_demand_weekly` 6,182; `agg_salary_by_role_city` 196; `snap_postings` **11,510** (9,516 current versions).
- Bronze: 82,849 observation rows over 10 days (2026-11-10 to 11-19), 10,568 `posting_content` rows.

**MotherDuck size (`scripts/storage_projection.py`, read from `md_information_schema.storage_info`).** `PRAGMA database_size` is useless on MotherDuck (0 bytes, NULL block size), which is why the script crashed. Now:
| Component | MiB |
|---|---|
| active | 134.3 |
| historical | 269.6 |
| failsafe | 384.5 |
| **total, all components** | **788.4 (7.7% of 10 GiB)** |
Active data alone is 134.3 MiB (1.3% of the limit). **Which components count against the Lite plan's 10 GB is the plan's rule; I could not determine it, so both ratios are shown.** The historical and failsafe parts are large because the bronze migration and the catch-up rewrote tables, and MotherDuck keeps changed data for the database's snapshot retention (7 days) plus failsafe; I expect them to shrink as that data ages out but have not observed it, so re-check in about two weeks. For scale: the earlier reading on 2026-11-10 (old bronze shape, one day) was 54.7 MiB. The local projection (about 3.5 MiB/day, about 105 MiB per 30 days) is an estimate from local DuckDB, not yet confirmed by day-over-day MotherDuck readings; run `scripts/storage_projection.py` daily or weekly and compare the active figure.

**Fixes**
1. The CLI now reads `JOBPULSE_BUCKET` from `.env` inside Python (same loader as the tokens), so S3-backed commands no longer need it exported. An exported value still wins; with neither, the command refuses as before. Two tests.
2. Storage reporting works on MotherDuck (above); locally it uses used blocks. `gold_report.py` no longer prints a misleading `database_size: 0 bytes`; it points to the storage script. One test covers the script on a local file and the missing-view case. The MotherDuck branch itself is only verified by my live run just now, not by a unit test.

**P4 is complete** (all four boxes ticked in ROADMAP). Not yet seen: the daily `gold` job in Actions after the outage (next scheduled run, around 17:00 UTC), and extraction of the 3 outage days.

**Checks run (`make check`, final):** ruff check and format clean; mypy strict 41 files; pytest **313 passed** (3 live deselected); terraform fmt + validate both roots valid; gitleaks no leaks.

**Owner steps**
1. Check the next scheduled Daily ingest: all four jobs green, and `extract` clears the outage-day postings (`gold_report.py` "with extraction" should rise above 481).
2. Add `MOTHERDUCK_TOKEN` to the GitHub `prod` secret if you have not (the new token), otherwise the scheduled warehouse job still fails.
3. In about two weeks run `uv run python scripts/storage_projection.py` and tell me the active and total figures.

---

## 2026-11-19 (late): P5 started: LCA loader, employer matching, match-review tooling (no real data yet)

**Plan approved by the owner** (FY2024, FY2025, FY2026 full year or latest quarter; gold models over ops/LLM calls so the app reads gold only; a ~50-pair blind employer-match sheet the owner labels; commit per step). This entry covers the first two steps. Nothing has been loaded from real DOL files: **dol.gov returns 403 to automated requests, and I did not work around that**, so everything below is tested only on synthetic DOL-shaped files.

**What was built**
- `data/oflc/` is gitignored (manual downloads, large).
- `jobpulse load-lca [--dir data/oflc] [--db FILE]` reads `.xlsx` (DuckDB's built-in `read_xlsx`, so **no new dependency**; the excel extension downloads once on first use) or `.csv`. Configured in `config/lca.yaml`: fiscal years, accepted column names per canonical column, required columns, certified statuses, match settings, review size. It keeps only H-1B (exact `VISA_CLASS`; H-1B1 and E-3 are excluded), only the listed columns (attorney, agent, contact and FEIN columns are never read), de-duplicates on `CASE_NUMBER` (latest decision wins, so overlapping quarterly files are fine), and fails loudly with the file's column list if a required column is missing.
- Tables in schema `ref` (new `warehouse.ref_schema`): `lca_employer_names` (every H-1B employer name per year with counts), `employer_lca_match`, `lca_disclosures` (**matched employers' filings only**). Loading a fiscal year replaces that year's rows; reruns are idempotent. A run record (`task=lca`) goes to `ops.pipeline_runs`.
- **Matching is deterministic** (`visa/normalize.py`, `visa/match.py`): `exact`, `normalized` (punctuation and legal suffixes removed), and `alias` (`config/lca_aliases.yaml`, owner-reviewed). Confidences: exact 1.0, alias 1.0, normalized 0.9; threshold 0.9. A name that matches two companies is ambiguous and not matched. Similarity is only used to *suggest* candidates for review, never as a match. `config/lca_aliases.yaml` is empty for now: I will not guess legal-entity names before I see the data.
- `eval/employer_match_sample.py` writes a **blind** sheet (`pair_id, company, lca_employer_name, n_cases_all_years, label`) mixing our matches (about 60%) with similar-but-not-matched names; which is which, and the method, are only in `eval/employer_match_manifest.json`. `eval/employer_match_score.py` reads `eval/employer_match_labels.csv` and writes `eval/reports/employer_match_report.md`: precision of our matches overall and by method with a 95% Wilson interval, wrong matches, and "missed matches" (suggested pairs labeled yes, to become aliases).
- Unrelated fix found on the way: `configure_logging` kept whatever `sys.stderr` existed at first use, which broke later tests after a CLI test under pytest capture; it now resolves `sys.stderr` at write time. `bulk_insert` moved from `gold.py` to `warehouse.py` (shared).

**Not proven yet (needs the real files)**
- That the real DOL headers match `config/lca.yaml` (I used the layout from memory: `CASE_NUMBER, CASE_STATUS, RECEIVED_DATE, DECISION_DATE, VISA_CLASS, JOB_TITLE, SOC_CODE, SOC_TITLE, FULL_TIME_POSITION, TOTAL_WORKER_POSITIONS, EMPLOYER_NAME, WORKSITE_CITY, WORKSITE_STATE, WAGE_RATE_OF_PAY_FROM/TO, WAGE_UNIT_OF_PAY, PREVAILING_WAGE, PW_UNIT_OF_PAY, PW_WAGE_LEVEL`), how date cells arrive from `.xlsx` (the code accepts ISO dates and Excel serial numbers), load time and memory on a 100+ MB workbook, and how many of the 43 companies match.
- Real match precision, and how many matched pairs exist for the 50-pair target. **If fewer than about 30 of our matches exist, the sheet fills with near-miss suggestions, and the precision figure rests on fewer pairs than 50.** The report states both numbers.

**Checks run (`make check`, final):** ruff, format, mypy strict (45 files), pytest **343 passed** (3 live deselected; +40 for LCA loading, matching and review), terraform fmt + validate both roots valid, gitleaks no leaks. Not run: anything against the real DOL files or MotherDuck for `load-lca`.

**Owner steps (now)**
1. Download the DOL files into `data/oflc/` (list in the session reply) and tell me.
2. Then I will run `load-lca` locally against the files to confirm the layout, show you the match results, draft alias candidates for you to review, and continue with the gold visa tables.

---

## 2026-11-20: P5 on real DOL files: load, aliases, blind sheet, gold visa tables

**Scope change (owner):** FY2025 and FY2026 only (8 quarterly files, ~730 MB). FY2024 is an optional later add (put the files in `data/oflc/` and add 2024 to `config/lca.yaml`).

**Real-data results** (scratch local DuckDB, not MotherDuck; produced by `jobpulse load-lca`)
- The files are per-quarter, not cumulative; `CASE_NUMBER` is unique within each. Rows per file: FY2025 Q1-Q4 107,414 / 132,133 / 238,425 / 118,580; FY2026 Q1-Q4 82,477 / 121,912 / 230,263 / 119,207. Total 1,150,411; H-1B 1,121,941; distinct cases after de-duplication 1,109,292; 91,472 distinct employer names.
- The DOL headers matched `config/lca.yaml` with no changes; dates and wages parsed in every stored row. Load: about 70 s, 1.4 GB peak memory.
- Matching before aliases: 32 of 43 companies. After the owner accepted 10 aliases (Chime Financial, Notion Labs, Palantir Technologies, Peloton Interactive, Ramp Business Corporation, Robinhood Markets, Spotify USA, Maplebear = Instacart, eShares = Carta, Social Finance LLC = SoFi; "SoFi Bank N.A." excluded): **42 of 43**, 10,605 filings stored. Betterment has no H-1B filings in FY2025-FY2026 (shown as such, not as an error).
- Only plain `CERTIFIED` counts as evidence (owner decision). `CERTIFIED - WITHDRAWN`, `WITHDRAWN` and `DENIED` are counted in separate columns.

**What changed**
- `config/lca_aliases.yaml`: 10 reviewed aliases (confidence 1.0).
- Blind sheet `eval/employer_match_labels_template.csv`: 60 pairs, **36 are our matches**, 24 near-misses. So the matched-pair precision rests on at most 36 labels, below the 50-label target in the ROADMAP; the report prints both numbers. The ROADMAP box stays unticked until the owner labels and the script reports.
- Gold (dbt): `agg_lca_company_year`, `agg_lca_company_role`, `agg_lca_wage_level`, `agg_lca_wage_by_soc_city` (offered wage, never blended with advertised salary), working tables `int_lca_certified` / `int_company_lca` in `gold_stage`; `dim_company` gains match confidence, LCA counts and `visa_evidence_class`; `fct_postings` gains `sponsorship_quote` and `sponsorship_class`. `ref` is a dbt source. `build-gold` creates empty `ref` tables when `load-lca` never ran, so gold still builds without visa data.
- Role family for LCA titles reuses the extraction title rules; unmatched titles are `other` (so by-role counts are lower bounds for the target families).
- `tests/unit/test_gold_visa.py` (7): empty ref, statuses, evidence threshold, posting-over-LCA precedence, mixed company evidence, role family, wage level, hourly annualization.

**Not proven yet**
- Gold visa tables are tested on synthetic data only; they have not been built on MotherDuck. That needs `load-lca` against MotherDuck first (owner step 2).
- Match precision (needs the owner's labels). Role-family mapping of LCA titles has not been reviewed on real titles.

**Checks run (`make check`):** ruff, format, mypy strict, pytest **350 passed** (3 live deselected), terraform fmt + validate both roots valid, gitleaks no leaks.

**Owner steps**
1. Label `eval/employer_match_labels_template.csv`: copy it to `eval/employer_match_labels.csv`, fill `label` with yes / no / unsure per row, then run `uv run python eval/employer_match_score.py` (writes `eval/reports/employer_match_report.md`).
2. Load the LCA data into MotherDuck: `uv run jobpulse load-lca` (about 70 s, about 10.6k filing rows plus 91k employer names; small against the 10 GB limit), then `uv run jobpulse build-gold`. Tell me, and I will check the gold counts and the evidence classes.

---

## 2026-11-20 (later): P5 visa gold checked on real MotherDuck data

Read-only queries against MotherDuck (token from `.env` inside Python; the scripts are throwaway, not in the repo). `ref` holds 10,605 filings, 134,481 employer-name rows and 42 matches. Gold built 88/88 tests. MotherDuck storage after the load: active 71.5 MiB, historical 412.7 MiB, failsafe 384.5 MiB (868.7 MiB all components; was 788.4 MiB).

**Counts**
- Certified (plain CERTIFIED) LCAs: FY2025 4,727 (41 companies), FY2026 5,102 (42). Certified-withdrawn 302 / 375, withdrawn 52 / 45, denied 0 / 2. Total certified 9,829.
- `dim_company.visa_evidence_class` (43 companies): HISTORICAL_EVIDENCE 39, EXPLICITLY_UNAVAILABLE 2 (Scale AI, EverQuote), CONFIRMED_OFFERED 1 (Affirm), UNKNOWN 1 (Betterment, no filings).
- Wage level of certified LCAs: I 1,130; II 2,741; III 2,375; IV 3,262; unknown 321.
- `fct_postings.sponsorship_class`: HISTORICAL_EVIDENCE 9,475 (8,430 active), EXPLICITLY_UNAVAILABLE 11 (8 active), CONFIRMED_OFFERED 2 (1 active), UNKNOWN 28 (25 active). Only 481 postings have been extracted, so only 13 postings carry an explicit statement; the other 9,475+ are classed from the company's LCA history. A posting that was never extracted has not been read, so `HISTORICAL_EVIDENCE` there means "the employer has filings", not "the posting is silent". The app must say so.

**Role-family mapping spot check** (job titles of certified LCAs, same rules as extraction): other 8,175; data_scientist 568; ml_ai_engineer 558; data_analyst 233; data_engineer 187; analytics_engineer 53; bi 32; business_analyst 23.
- Top titles per family are right (Data Scientist, Machine Learning Engineer, Data Engineer, Analytics Engineer, BI Analyst, ...). Manager titles that contain the keyword map to the family ("Manager, Analytics" is data_analyst; "Engineering Manager, Machine Learning" is ml_ai_engineer): the family is a title keyword match, not a seniority split.
- `other` is 83% and is mostly Software Engineer (2,249 plain) / Product Manager / Designer, which are outside the target families. Genuine misses: "Software Engineer, Data" (10), "Research Scientist" (about 22), "AI Engineer" (about 20, excluded on purpose by the ML/AI narrowing), generic "Analyst" titles. So by-role counts are **lower bounds**; I did not change the rules (they are the owner's extraction rules).
- Palantir shows 292 of 297 in `other` (Software Engineer, Forward Deployed Engineer): its data-role count is small because its titles are generic, not because the match is wrong.

**Problems found (not fixed, need an owner decision)**
1. **Wage unit errors in the filings.** 22 certified LCAs annualize to more than $1M (max $439M). Of 9,829 certified filings, 28 have a unit other than YEAR (13 HOUR, 7 WEEK, 8 MONTH) and their median amount is 142k-187k, so the filer labelled an annual figure with the wrong unit. Medians stay sane (122 groups have percentiles, p50 from 81.6k to 262.5k) but p75 and any mean can be hit. Proposed fix: a configured plausible annual range (for example 15k to 2M); outside it the wage is NULL and counted in coverage, never repaired by guessing. No change made.
2. **Company-level class can mislead.** Scale AI is EXPLICITLY_UNAVAILABLE because 3 postings say "We do not sponsor visas for Fellows" (a fellowship), while it has 143 certified LCAs. Affirm is CONFIRMED_OFFERED from one Spain-based posting ("able to offer visa sponsorship ... based in Spain") while it has 188 US certified LCAs and two postings that say not available. EverQuote is EXPLICITLY_UNAVAILABLE on 3 postings. A posting statement is about that posting. Proposed change: `dim_company.visa_evidence_class` reflects LCA history only (HISTORICAL_EVIDENCE / UNKNOWN) and the posting statements stay at posting level (`fct_postings.sponsorship_class` and quote) with the counts shown next to it. No change made; this changes the rule in ARCHITECTURE.
3. Mercury is HISTORICAL_EVIDENCE on 3 certified filings (confidence 0.9). It passes the threshold; the app should always show the count beside the class.

**Checks:** read-only queries only; no code changed, so `make check` was not re-run for this entry.

**Open questions for the owner**
1. Apply the wage plausibility range (and what range)?
2. Make the company-level class LCA-only, with posting statements shown per posting?

---

## 2026-11-20 (later still): owner decisions applied (wage range, company class)

- `config/lca.yaml` `wage.plausible_annual_min/max` = 15,000 / 1,000,000 USD/year. An annualized offered wage outside it is NULL (`int_lca_certified.wage_excluded`), never repaired; `agg_lca_wage_by_soc_city` has `n_wage_excluded` and coverage = n_with_wage / n_certified. The filings still count as evidence.
- `dim_company.visa_evidence_class` is filing history only (HISTORICAL_EVIDENCE / UNKNOWN); posting statements stay per posting with `n_active_offered` / `n_active_not_offered` on the company row. The app must show `n_lca_certified` next to the class and label by-role LCA counts as lower bounds. ARCHITECTURE.md updated.
- Checks: `make check` green, pytest 351 passed. Not yet rebuilt on MotherDuck: run `uv run jobpulse build-gold` to apply (no reload of LCA needed).

---

## 2026-11-20 (evening): P5 Pipeline Health gold models and the Streamlit app

**What was built**
- Gold: `fct_snapshot_freshness`, `fct_pipeline_runs`, `agg_ingest_status_daily`, `agg_data_quality`, `agg_llm_cost_daily` (over `ops.*`, `bronze.snapshot_pointers`, `extract.llm_calls`), so the app reads gold only. `build-gold` creates empty ops tables if missing. Verified on the real data shape: 120 run rows, 506 board snapshots and 675 LLM calls all had unique grains.
- `src/jobpulse/app_data.py`: the only door to the warehouse (single read-only SELECT, gold allow-list, read-only connection). `src/jobpulse/app_ui.py`: cached reads (1 h), wording, charts. `app/streamlit_app.py` + five pages. No new dependency (pandas and Streamlit's own charts).
- 24 tests in `tests/unit/test_app.py` (AppTest on every page with and without data, stale banner, visa disclaimer and quote and counts, separate salary tabs, refused statements, read-only connection, gold-only static check). The bare-warehouse test caught a real bug (a company with no filings crashed the Visa page); fixed.
- Ran the app headless against a fixture warehouse with the Streamlit agent API: all pages load, no warnings in the server log. **Not run against MotherDuck or your real data in a browser.**

**Not proven yet / caveats**
- Layout is responsive by construction (stacked columns, no fixed widths) but I have not looked at it on a phone or in a browser; the tests check content, not appearance.
- `MOTHERDUCK_READ_TOKEN` path (read-scaling token) is untested against MotherDuck.
- ROADMAP P5 boxes are not ticked yet: they need CI green on the push and your look at the pages; the employer-match precision box needs your labels. Gold needs `build-gold` on MotherDuck to pick up the new models and the wage range/class changes.
- Pipeline Health will show a stale banner whenever the newest snapshot is older than 30 h (the real data currently ends 2026-11-19 13:18 US Eastern, so it may already show one).

**Checks:** `make check` green, pytest 377 passed (3 live deselected).

**Owner steps**
1. `uv run jobpulse build-gold` against MotherDuck (applies the wage range, company class and health models).
2. In MotherDuck create a read-scaling token, then run `MOTHERDUCK_READ_TOKEN=<token> uv run streamlit run app/streamlit_app.py` in your own terminal (the app reads the environment only, not `.env`; do not commit the token) and look at all five pages (phone width too: browser dev tools). Tell me what looks wrong.
3. Label the employer-match sheet (see the earlier entry).

---

## 2026-11-20 (night): app review fixes after the owner's look at the real pages

- **Labels and money:** human labels everywhere (role, seniority, remote policy, run step, result, source), column headers renamed, salary shown as `$276K` (whole thousands, no decimals); AI spend stays at the cent (`$0.49`) because it is small. Pipeline Health charts use neutral blue/grey (no red).
- **Market overview:** pitch line, GitHub link, "Data & AI roles analyzed: 441" with the explanation that the AI reads only data/AI roles chosen by title. Coverage of those roles is **441 of 511 open target-role postings (86%)**; the other 7,953 open postings are counted but not read. New `fct_postings.is_target_role` and metric `target_roles_analyzed` (agg_data_quality).
- **Hiring trends:** new and closed postings per day (ISO date labels, no hourly axis); the first day of data is left out and captioned. **The first day in the data is 10 Nov 2026, not 9 Nov** (first snapshot date; 9 Nov was the day the extractor was first run). Skills use week-start dates.
- **Salary city "unspecified" (cause found):** not every row, but the biggest bucket (207 of 323 salary-bearing postings). Their locations are remote ("Remote - United States"), multi-location ("San Francisco, CA; New York, NY") or a country only, so by the city rule they have no single city. Not a bug. Fix: new gold model `agg_salary_by_role` (no city) for "All cities"; a City filter (with "No single city (remote or multi-location)") uses the city table; chart of median advertised salary by role family and seniority (groups with at least five salaries).
- **Visa:** offered/not-offered columns removed from the company table; the company drill-down says "Not stated" when nothing was analyzed, otherwise "N analyzed: x offer, y not available, z do not say". Match shown in words (Reviewed alias / Exact name match / Name match). Top-sponsors bar chart with a role-family filter (default: all data roles; lower bounds). Matched filing names now show real casing ("DoorDash, Inc.") instead of the normalized form. Timestamps on Pipeline Health are converted to UTC to match their label.
- **Verified on real MotherDuck data** (build-gold 107/107 models+tests passed, 87 tests, 0 failures; all five pages rendered through AppTest with no exceptions and the figures above). Not verified in a browser or on a phone.
- **Checks:** `make check` green, pytest 381 passed.

---

## 2026-11-20 (late night): second review polish

- **Charts:** replaced Streamlit's built-in bar/line charts with small Vega-Lite specs (`app_ui.bar`, `date_chart`, `salary_chart`; no new dependency): category labels are never truncated (label limit 600 px), bars carry value labels, dates read "Nov 11" (horizontal), one tick per day. The salary chart is grouped horizontal bars, role family on the axis, seniority as colour (light to dark blue as seniority rises), `$NNNK` on every bar. Specs were validated against the Vega-Lite schema (Streamlit ships Vega-Lite 6, which supports the grouped-bar offset).
- **Theme:** `.streamlit/config.toml` now sets `primaryColor = "#2F6DB5"` and `client.toolbarMode = "viewer"` (no developer toolbar or Deploy button). The file is at the repo root, so it applies when the app is started from the repo root, as in the run command. Tabs, filters and progress bars follow the primary colour.
- **Labels:** acronyms are kept (`AI`, `ML`, `BI`, `LCA`, `USD`); the data-quality descriptions no longer lower-case "AI" (the old `str.capitalize()` did). Table text columns have explicit widths so cells are not clipped.
- **Not verified:** I cannot render Vega-Lite here, so the look of the charts is unchecked beyond schema validity and the pages running without errors on real data; the theme colour is checked by a test of the config only. Please look at the salary chart first.
- **Checks:** `make check` green, pytest 383 passed.

---

## 2026-11-20 (final): employer-match scored; Mercury, subsidiaries, live reload; P5 boxes

**Labels and score.** 60 of 60 pairs labeled; the labels were drafted with AI assistance and reviewed by the owner (the report now says so instead of "Labels are the owner's"). Precision of our matches: **100% (35 yes, 0 no; Wilson 95% interval 90.1% to 100%)**, 1 unsure (P011 "Mercury, Inc."). By method: alias 8/8, exact 2/2, normalized 25/25 (+1 unsure). Report: `eval/reports/employer_match_report.md` (the report also states that matches added after the sheet was drawn are not in the sample). Four suggested pairs labeled yes were the missed subsidiaries below.

**Mercury (verified against the filings).** "Mercury, Inc." (3 certified filings, all FY2026) is **removed**: its titles are "Forward Deployed AI Engineer", "Forward Deployed Product Manager" and "Forward Deployed AI Architect" at worksites in Santa Monica CA, New York NY and San Francisco CA, a different kind of company from the fintech. I cannot prove that from the filings alone; it is excluded because the name is ambiguous (any "Mercury") and the evidence points away. `config/lca_aliases.yaml` now has `excluded: mercury: ["Mercury, Inc."]` and the alias **"Mercury Technologies, Inc." (24 filings)**, the fintech's legal name. Its titles fit the fintech (Senior Data Scientist, Senior Software Engineer, Senior Risk Strategist, Product Designer at New York and San Francisco); two outliers I noticed are a "Senior Model Risk Manager" in Norwalk CT and a "Software Engineer II" in Santa Monica, so treat the 24 as likely, not certain. Mercury went from 3 to 24 certified filings (still the thinnest evidence after Betterment).

**Subsidiaries.** Reviewed and marked `relationship = 'subsidiary'` in `ref.employer_lca_match`, counted for the parent and shown in the app and in `dim_company.lca_subsidiaries`: DoorDash Essentials, LLC (4), Airbnb Payments, Inc. (4), SoFi Bank N.A. (15), Snowflake Public Sector, Inc. (2). New config keys `subsidiaries` and `excluded` (`LcaAliases`), new column `ref.employer_lca_match.relationship` (added to existing tables on the next load).

**Live run on MotherDuck.** `load-lca`: 42 of 43 companies matched, 46 LCA names, **10,651 filings** (was 10,605). `build-gold`: 87 tests, 0 failures. Certified filings now 9,874 (was 9,829). Evidence classes: HISTORICAL_EVIDENCE 42, UNKNOWN 1 (Betterment).

**P5 boxes.** Ticked: pages read gold only, salary coverage and separate views, visa disclaimer and evidence fields, AppTest in CI, freshness banner. **Not ticked:** "employer-match precision on at least 50 labeled employers": 60 pairs are labeled but only 35 are our matches (the rest are near-misses), so the precision rests on 35. Your call whether that meets the line.

**App polish from the last message** was already done and pushed (grouped salary chart with $K labels, blue theme, viewer toolbar, "Nov 11" dates, no truncated labels, AI capitalization).

**Checks:** `make check` green, pytest 386 passed.

---

## 2026-11-20 (close): P5 marked complete

Owner accepted 35 matched pairs (of 60 labeled) for the employer-match precision line; the box is amended to "on ≥ 50 labeled pairs (60 labeled; precision computed on the 35 matched pairs)" and ticked. All P5 boxes are ticked and P5 is marked complete in `docs/ROADMAP.md`. Docs-only change; `make check` not re-run (no code or config changed). Next: P6 (Resume Match and deployment); not started.

---

## 2026-11-20 (P6): Resume match, token-free public deploy, README

**Owner decisions this session:** add `pypdf` (app group only, 2 MB cap, reject non-PDF/text); add a `publish` job and publish the allow-listed gold tables publicly; Option A for deployment; pin Python 3.12 on Streamlit Cloud and add a requirements file if needed.

**Part A: Resume match (no LLM, $0 per visit)**
- `config/resume.yaml` (new, `ResumeConfig`): upload limits (2 MB, 10 PDF pages, 60,000 characters, pdf/txt/md), parsing rules, match settings.
- `src/jobpulse/resume/parse.py`: pasted text or PDF in memory; extension, size and magic-byte checks; encrypted/scanned PDFs refused with a message. Skills are found by looking up 1 to 4 word n-grams, normalized with `norm_skill` (the function that built `dim_skill`), in `gold.dim_skill`. Single letters and everyday words (R, C, Go, Ray, search, testing, ...) count only in a configured form. Output is a `ResumeProfile` (canonical skills, up to 5 short title lines, a years figure); the raw text is never part of it.
- `src/jobpulse/resume/match.py`: score = matched skills / the posting's skills; postings with fewer than 5 skills are not ranked; ties break by matched count, company, title.
- `app/app_pages/resume_match.py` + a sixth navigation entry: privacy notice, paste/upload, detected skills, filters (role, remote, city, visa evidence class), table with match %, matched and missing skills, visa evidence class with filing count, advertised salary, and the disclaimer. Sponsorship is a filter only. **Delete my resume data** clears every `resume_*` session key.
- Real data check (read-only on MotherDuck, via the exported file): all six pages run without exceptions; a sample data-scientist resume produced sensible top matches (Brex, Stripe, MongoDB, Pinterest, Lyft); 436 postings were rankable at the time of the first check.
- Found and fixed in testing: trailing punctuation ("dbt.") stopped a skill from matching.

**Part B: public deploy with no database token (Option A)**
- `config/publish.yaml` (new, `PublishConfig`); `src/jobpulse/publish.py`; CLI `jobpulse export-gold`. It copies only `app_data.GOLD_TABLES` into a fresh DuckDB file and validates it (exactly those tables, row counts equal to the source, per-table minimums) before an atomic move. A failure leaves the previous file untouched.
- `app_data.connect()` gains the published-file mode (`JOBPULSE_GOLD_URL`, only URLs under the configured release prefix; the download must hold exactly the allow-listed tables; stale copy kept if a refresh fails). `HttpClient` got `expect="bytes"`. The app's connection cache now expires hourly so the file is re-checked. The URL can also come from Streamlit "Secrets" (`st.secrets`).
- Found and fixed: DuckDB names a database after its file, so a file called `gold.duckdb` makes `gold.table` ambiguous. The asset is `jobpulse_gold.duckdb`, and config validation rejects `gold.duckdb`.
- `daily_ingest.yml`: new `publish` job (`needs: gold`, only when gold succeeded). `contents: write` on that job only; `MOTHERDUCK_TOKEN` on the export step only, the built-in token on the upload step only; no AWS or OpenAI credentials. A test pins these rules and the tag/asset names against `config/publish.yaml`. **The job has not run in GitHub Actions yet.**
- `app/requirements.txt` (Community Cloud reads the file next to the main file first): eight pinned packages, checked against `uv.lock` by a test. `streamlit_app.py` adds `src/` to `sys.path` because the project is not pip-installed there. Verified in a clean Python 3.12 venv with only that file installed: all six pages run against the real export.
- Real export from MotherDuck (read-only): 5.3 MB, 16 tables, 9,516 postings.
- `.streamlit/config.toml`: `maxUploadSize = 2` (MB).
- The `MOTHERDUCK_READ_TOKEN` path stays for development only and is untested (the Lite plan has no read-scaling token).

**Part C: README** rewritten: live-link and screenshot placeholders (they need the deploy), Mermaid diagram, demo script, key numbers each with the script that produced them, limitations, future work. `scripts/gold_report.py` gained two lines (companies with certified filings; certified filings matched) so the LCA figures come from a script.

**Checks run**
- `make check`: see the last run below. Before the final doc edits it was green: ruff, ruff format, mypy strict (51 files), pytest 427 passed (3 live deselected), terraform fmt and validate for both roots, gitleaks no leaks.
- Not verified: the `publish` job in GitHub Actions, the Streamlit Cloud build (Python 3.12 selection, `app/requirements.txt` pickup), a browser look at the Resume match page, phone layout, the PDF path on real resumes (only a synthetic one-page PDF was tested), and that Streamlit Cloud exposes the secret to `st.secrets` as assumed.

**Open for the owner**
- The steps below. P6 boxes "deployed" and "README has the live link" stay unticked until then.
- The README's live link and screenshots are placeholders.
- The MotherDuck token pasted into a session transcript on 2026-11-09 was never rotated (still recommended; the public app no longer depends on it).

**Owner steps**
1. Ask me to commit (small commits on `main`), then push.
2. GitHub: Actions tab, "Daily ingest", "Run workflow" (branch `main`, leave snapshot_date empty). When all jobs are green, the repo's Releases section has "Gold data (latest)" with `jobpulse_gold.duckdb` (about 5 MB).
3. Streamlit Community Cloud: sign in at share.streamlit.io with GitHub, Create app, repository `akshaygovindd/jobpulse`, branch `main`, main file `app/streamlit_app.py`, Advanced settings: Python 3.12 and Secrets `JOBPULSE_GOLD_URL = "https://github.com/akshaygovindd/jobpulse/releases/download/gold-latest/jobpulse_gold.duckdb"`, then Deploy.
4. Send me the app URL; I add it to the README and tick the last two P6 boxes. Take the screenshots from the live app.

---

## 2026-11-20 (later): MotherDuck token rotated; open item closed

The owner rotated the MotherDuck token today: the new token is in the local `.env` and in the GitHub `prod` environment secret `MOTHERDUCK_TOKEN`, and the old tokens are revoked. This closes the open item from 2026-11-09 (the token that appeared in a session transcript). Nothing in the repo changed for this; I did not see or handle the new value. The public app does not use this token (it reads the published gold file).

README: added a Highlights section near the top, and changed the architecture diagram's visitor edge to the plain `-->|label|` syntax (the dotted-edge label form is the likeliest to fail in GitHub's Mermaid renderer). I could not render the page on GitHub from here; the diagram and tables still need a look on github.com after the push.

---

## 2026-11-20 (evening): P6 pushed; visual polish pass

**Commits pushed to `main`** (small, conventional, no trailer): `chore: add pypdf`, `feat: token-free public deploy ...`, `feat: resume match page ...`, `docs: README rewrite ...` (with the token-rotation entry and the README Highlights), then the polish pass: `feat: one shared chart style ... and blue theme`, `feat: Home page, data-computed key insights ..., KPI deltas`, `feat: map of open postings by city ...`, `feat: company lookup card on the Visa page`. CI was green on the first three pushes at the time of writing; later runs are on the commits' pages.

**Polish pass (no new dependencies; pydeck was already installed as a Streamlit dependency)**
- **Shared chart style:** one palette block and one Vega config in `app_ui.py`; every chart goes through one helper; label colour is a mid slate that works on light and dark. `.streamlit/config.toml` adds `linkColor` and `baseRadius`. Tests fail on a page-level chart or a hard-coded hex colour.
- **Home page** (default): hero, four cards computed from gold (open postings with weekly change, most requested skill, median advertised USD pay, companies with H-1B filings), resume-match call to action, visa disclaimer. Market overview lost its hard-coded "43 tech employers" line; the number now comes from the data.
- **Key insight lines** (`insights.py`, pure and unit-tested) above the company, role, remote, city, daily-flow, skill, salary and sponsor charts. Found while checking real output: "Not stated" was being named the most common remote arrangement, and an "average per day" would have counted only days with activity; both fixed (the sentence now skips "Not stated" and reports totals, not averages).
- **KPI deltas** on open postings, companies and cities. A first version compared the newest snapshot day with a week earlier and would have overstated growth by roughly one day of closures (a posting counts as closed only after two missed snapshots), so both ends now use the newest *settled* day. On the real data the open-postings change is small (+5 vs last week); treat it as indicative. Deltas are neutral-coloured and absent when the history is too short.
- **Map:** pydeck, CARTO dark basemap (no token), attribution under the map from `config/app.yaml`. Positions are 103 hand-entered approximate city centres (`config/city_coords.yaml`). On the real export 4,862 of 5,285 city-named postings are drawn and 423 are in cities without an entry (the page says so). I typed the coordinates from general knowledge; spot-check a few.
- **Visa company lookup card:** type-ahead search, badge, counts, filings per fiscal year, strongest year, most common data role family (lower bound) and wage level, filed-under names, disclaimer; breakdown tables and quotes stay below.
- Tests added: `tests/unit/test_app_style.py` (style rules, Home cards and links, KPI delta with a 4-day fixture, no delta when history is short, key insights, map element and attribution, map point maths, coordinates and app config, company card) and `tests/unit/test_insights.py` (every sentence).

**Checks run:** `make check` after each step: ruff, ruff format, mypy strict (52 files), pytest 449 passed (3 live deselected), terraform fmt and validate for both roots, gitleaks no leaks. A final full run after the docs edits (this entry, ARCHITECTURE, README, SPEC) was also green. Real-data checks (read-only export from MotherDuck): every page runs without exceptions; the sentences and numbers above were read from that run.

**Not verified:** how any of this looks in a browser. AppTest checks content and element structure, not rendering: the map tiles and dots, the cards' spacing, the dark-theme legibility of chart labels, phone layout, and whether pydeck's CARTO basemap loads from Streamlit Community Cloud (it fetches tiles in the visitor's browser). Please look at Home, Market overview (the map) and the Visa card on the deployed app.

**Open for the owner:** the deploy steps from the P6 entry are still pending (first `publish` run, then Streamlit Cloud). The published export now carries the same gold tables; no export change was needed.
