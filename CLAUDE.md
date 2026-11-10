# CLAUDE.md — JobPulse working agreement

Read first: [docs/SPEC.md](docs/SPEC.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/ROADMAP.md](docs/ROADMAP.md).

## Scope
- Scope is **frozen**. Ask the owner before adding any tool, source, feature, or dependency.
- Thin slice before breadth: finish the current phase's "done when" checks before starting the next phase.

## Stack
Python 3.12 · `uv` (env + lockfile) · `httpx` · `tenacity` · `pydantic` v2 · `structlog` (JSON) · `boto3` · `duckdb` / `dbt-duckdb` + MotherDuck (from P2) · `streamlit` · `pytest` · `ruff` · `mypy --strict` on `src/` · Terraform · `gitleaks` in CI.
LLM: OpenAI. A cheap small model first (`gpt-6-luna`), escalating once to a stronger one (`gpt-6.1-terra`) on validation failure, with Structured Outputs. Provider, model ids, prices and spend caps live in `config/extraction.yaml`, never in code. `OPENAI_API_KEY` is read from the environment (GitHub `prod` secret in CI; `.env` loaded from Python only locally, never sourced in a shell, never printed or logged). Only postings in the target role families (title filter in config) are sent to the LLM.

## After every change
1. Run `make check`. It runs `ruff check`, `ruff format --check`, `mypy`, and `pytest`. When `infra/` changed, it also runs `terraform fmt -check -recursive` and `terraform init -backend=false && terraform validate` in each root.
2. Never report a task done while checks are red. Quote failures verbatim.
3. If a check was skipped, say so and why.

## At the end of every session
Append a plain-English entry to [docs/NOTES.md](docs/NOTES.md) with:
- the date
- what changed and why
- the checks you ran and their results
- open questions for the owner

## Infrastructure boundaries
- Claude writes Terraform code and runs only `fmt` and `validate`.
- Claude **never** runs `terraform plan`, `apply`, or `destroy`. The owner runs plan and apply locally.
- No secrets in the repo, ever.
  - Runtime secrets are GitHub **`prod` environment** secrets.
  - `.env.example` and `terraform.tfvars.example` hold placeholders only.
  - `*.tfvars` and `.env` are gitignored. The budget alert email lives in a tfvar.
- AWS access from CI uses GitHub OIDC only. No access keys anywhere.

## Conventions
- **Config-driven.** Everything tunable lives in `config/*.yaml` and is validated by Pydantic models. No magic numbers in code.
- **Connectors.** All connectors implement one `Connector` protocol with explicit completeness signalling: a fetch is either complete or carries a reason code.
- **HTTP.** All HTTP goes through the shared client in `src/jobpulse/http.py`. It handles exponential backoff with jitter, honours 429 `Retry-After`, and enforces timeouts.
- **Isolation.**
  - A failing company or source must never raise past the runner. Catch the error, record a reason code, and continue.
  - Failed, truncated, or rate-limited fetches never imply closure.
- **Snapshot identity.**
  - A logical snapshot is identified by (source, company, `snapshot_date`). Each execution has its own `attempt_id`.
  - Raw data is append-only per attempt.
  - Downstream code reads **only** the authoritative pointer for each logical snapshot (see ARCHITECTURE.md).
- **Posting identity.** `posting_key = hash(source_id ‖ external_posting_id)` and `content_hash` detects changes. Rerunning the pipeline updates observation metadata. It never creates duplicate postings.
- **Closure.** A posting is closed only when it is absent from 2 consecutive authoritative snapshots for that company, or when the source reports an authoritative closed status. Adzuna is never used for closure.
- **Salary.** Advertised salary and LCA wages are separate datasets and are never blended. Always show coverage %.
- **Visa.** Visa data is evidence, never a promise. Sponsorship is a filter, never part of the match score.
- **Logging.** Logs are structured JSON with `attempt_id`, `snapshot_date`, `source_id`, `company`, `event`, and `reason` fields.
- **Time.** Use UTC everywhere with timezone-aware datetimes.
- **Tests.**
  - Unit tests use recorded fixtures in `tests/fixtures/` and make no network calls. Use `moto` for S3.
  - Live smoke tests are marked `-m live` and don't run in default CI.
- **dbt.** Name models with the prefixes `stg_`, `int_`, `fct_`, `dim_`, `agg_`, and `bridge_`. Every model has a uniqueness and not-null test on its grain.
- **Metrics.** Every reported metric is produced by a script, and the docs cite which script.
- **Git.** Make small commits with conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `infra:`). Commit only when the owner asks.
