"""LOCAL DEMO DAG (not production): ingest -> warehouse -> extract -> gold, on local storage.

Runs the same `jobpulse` CLI tasks as .github/workflows/daily_ingest.yml, but with `--env dev`,
local-directory storage and a local DuckDB file under /opt/demo-data. It cannot reach prod S3 or
MotherDuck: the container has no credentials, and the CLI's dev mode refuses a remote warehouse.

Trigger from the UI or: airflow dags trigger jobpulse_demo [--conf '{"real_extract": true}']
`extract` is a dry run unless real_extract is true (and docker-compose.extract.yml supplies a key).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from airflow import DAG
from airflow.models.param import Param
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

JOBPULSE = "/opt/jobpulse-venv/bin/jobpulse --env dev"
PYTHON = "/opt/jobpulse-venv/bin/python"
DATA = "/opt/demo-data"
STORAGE = f"{DATA}/storage"
DB = f"{DATA}/demo.duckdb"

FORBIDDEN_ENV = (
    "MOTHERDUCK_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "JOBPULSE_BUCKET",
)


def check_isolation(real_extract: str) -> None:
    """Fail the run before any work if the container could reach prod resources."""
    if os.environ.get("JOBPULSE_ENV") != "dev":
        raise RuntimeError("JOBPULSE_ENV must be 'dev' for the demo")
    present = [name for name in FORBIDDEN_ENV if os.environ.get(name)]
    if present:
        raise RuntimeError(f"demo container must not hold these variables: {present}")
    if os.environ.get("OPENAI_API_KEY") and real_extract != "True":
        print("note: an OpenAI key is present but real_extract is false: extract stays a dry run")
    print(f"isolation ok: dev env, local storage {STORAGE}, local database {DB}")


with DAG(
    dag_id="jobpulse_demo",
    description="Local demo of the jobpulse pipeline (not production)",
    start_date=datetime(2026, 11, 1, tzinfo=UTC),
    schedule=None,  # manual only
    catchup=False,
    max_active_runs=1,
    params={
        "sources": Param("", type="string", description="comma-separated: greenhouse,lever,ashby"),
        "real_extract": Param(False, type="boolean", description="make real OpenAI calls"),
        "extract_limit": Param(20, type="integer", description="postings per real extract run"),
    },
    tags=["jobpulse", "local-demo"],
) as dag:
    guard = PythonOperator(
        task_id="check_isolation",
        python_callable=check_isolation,
        op_kwargs={"real_extract": "{{ params.real_extract }}"},  # templated at run time
    )
    ingest = BashOperator(
        task_id="ingest",
        bash_command=(
            f"{JOBPULSE} ingest --local-dir {STORAGE}"
            "{% if params.sources %} --sources {{ params.sources }}{% endif %}"
        ),
    )
    load_ops = BashOperator(
        task_id="load_ops", bash_command=f"{JOBPULSE} load-ops --local-dir {STORAGE} --db {DB}"
    )
    load_bronze = BashOperator(
        task_id="load_bronze",
        bash_command=f"{JOBPULSE} load-bronze --local-dir {STORAGE} --db {DB}",
    )
    build_silver = BashOperator(
        task_id="build_silver", bash_command=f"{JOBPULSE} build-silver --db {DB}"
    )
    extract = BashOperator(
        task_id="extract",
        bash_command=(
            f"{JOBPULSE} extract --db {DB} "
            "{% if params.real_extract %}--limit {{ params.extract_limit }} --cap 0.05"
            "{% else %}--dry-run{% endif %}"
        ),
    )
    build_gold = BashOperator(task_id="build_gold", bash_command=f"{JOBPULSE} build-gold --db {DB}")
    report = BashOperator(
        task_id="gold_report",
        bash_command=f"cd /opt/jobpulse && {PYTHON} scripts/gold_report.py --db {DB}",
    )

    guard >> ingest >> load_ops >> load_bronze >> build_silver >> extract >> build_gold >> report
