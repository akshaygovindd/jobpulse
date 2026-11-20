# Local Airflow demo (not production)

Runs the same `jobpulse` CLI tasks as the daily workflow (ingest, load-ops, load-bronze,
build-silver, extract, build-gold) under Apache Airflow in Docker Compose. It is a **local demo of
orchestration**. Production is the GitHub Actions workflow in `.github/workflows/daily_ingest.yml`.

## What it can and cannot touch

| | Demo | Production |
|---|---|---|
| Raw/ops storage | local directory `airflow/demo-data/storage` | S3 bucket (`raw/`, `snapshots/`, `ops/`) |
| Warehouse | local DuckDB file `airflow/demo-data/demo.duckdb` | MotherDuck |
| Credentials in the container | **none** (no AWS, no MotherDuck token, no OpenAI key by default) | GitHub `prod` secrets |
| `extract` | dry run (no API calls, no cost) | real calls, capped |

The container gets no credentials and does not read the repo `.env` (no `env_file`, and `.env` is
excluded from the image build). On top of that the CLI runs with `--env dev`: it refuses any
warehouse that is not a local file, never reads the MotherDuck token, and (if S3 were ever used)
writes only under the configured `storage.dev_prefix`, which is validated not to overlap the
production key roots. The only network access needed is the public job-board APIs read by `ingest`.

## Run it

Needs Docker Desktop running. From the repo root:

```bash
cd airflow
docker compose up --build -d          # first build takes a few minutes
```

Wait about 20 seconds, then open <http://127.0.0.1:8080> (user `admin`, password `admin`) and
trigger the `jobpulse_demo` DAG. Or from the terminal:

```bash
docker compose exec airflow airflow dags trigger jobpulse_demo
docker compose exec airflow airflow dags list-runs -d jobpulse_demo          # state of the run
docker compose exec airflow airflow tasks states-for-dag-run jobpulse_demo "<run_id from list-runs>"
```

A full run takes about a minute: ingest ~20 s over the 43 public boards, then warehouse, extract
(dry run), gold, and a gold report. The report is in the `gold_report` task log (UI: DAG, run,
task, Logs). Data lands in `airflow/demo-data/` (gitignored).

Options when triggering (`--conf '{...}'` or the UI form):

- `sources`: e.g. `"ashby"` for a faster run (default: all sources).
- `real_extract`: `true` makes real, paid OpenAI calls (default `false`). It also needs the opt-in
  key file below.

### Opt in to real extraction (paid)

```bash
cp openai.env.example openai.env      # then edit: OPENAI_API_KEY=<your key>; this file is gitignored
docker compose -f docker-compose.yml -f docker-compose.extract.yml up -d
docker compose exec airflow airflow dags trigger jobpulse_demo --conf '{"real_extract": true}'
```

This limits a run to 20 postings and a $0.05 cap. The key goes into the container only through this
override file.

### Stop and reset

```bash
docker compose down                   # stop; data stays in demo-data/
rm -rf demo-data/storage demo-data/demo.duckdb   # start from empty next time
```

## Limitations

- Standalone Airflow with SQLite and the sequential executor. Fine for a demo, not for production.
- Tasks run once per manual trigger; there is no schedule. Ingest accepts only today's UTC date, so
  a run always reflects today.
- It does not exercise S3 or MotherDuck, so a green demo says nothing about those.
