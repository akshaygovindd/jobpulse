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
