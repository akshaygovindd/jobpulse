#!/usr/bin/env bash
# Local demo entrypoint: create a local admin user, then run Airflow's all-in-one "standalone" mode
# (SQLite metadata DB, SequentialExecutor). Not a production setup.
set -euo pipefail
airflow db migrate
airflow users create --username admin --password admin --firstname Demo --lastname User \
  --role Admin --email demo@example.invalid >/dev/null 2>&1 || true
exec airflow standalone
