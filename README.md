# JobPulse

Daily job market intelligence platform. It ingests public job postings into AWS S3 every day using Terraform-managed infrastructure and GitHub Actions authenticated through OIDC. Later phases add dbt models on MotherDuck, LLM field extraction, visa-sponsorship evidence, and resume matching.

- Product spec: [docs/SPEC.md](docs/SPEC.md)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Roadmap, risks and runbook: [docs/ROADMAP.md](docs/ROADMAP.md)
- Session log: [docs/NOTES.md](docs/NOTES.md)

## Status

**Phase 1: daily raw ingestion from Greenhouse and Lever.**

The pipeline covers 38 employers, listed in [config/employers.yaml](config/employers.yaml).

Each run, for every employer:
1. Fetches the full job board.
2. Writes the raw pages to S3 as an append-only attempt, with a manifest written last.
3. Publishes one authoritative pointer per source, company and day, but only when the snapshot is complete.

A run also writes these records as JSON under `ops/`:
- `pipeline_runs`
- `company_snapshots`
- `source_registry`
- the run report

## Development

```bash
uv sync                      # Python 3.12 environment
make check                   # ruff, mypy, pytest, terraform fmt/validate, gitleaks (if installed)
uv run pytest -m live        # optional smoke tests against the real public APIs

# Ingest today's boards into a local directory (no AWS needed)
uv run jobpulse ingest --local-dir .local_data
uv run jobpulse check-freshness --local-dir .local_data

# Re-probe every seeded board (add --write to refresh counts in employers.yaml)
uv run jobpulse validate-employers
```

`--snapshot-date` only accepts the current UTC date, so a missed day stays a gap and is never backfilled.

Source attribution:
- [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html)
- [Lever Postings API](https://github.com/lever/postings-api)

---

Developed with AI-assisted tooling (Claude Code). Architecture, evaluation design, and technical decisions are my own.
