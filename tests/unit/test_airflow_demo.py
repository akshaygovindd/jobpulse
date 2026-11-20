"""The local Airflow demo must not be able to reach production. Static checks on its files (the
real end-to-end run needs Docker and is done by hand; see airflow/README.md)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

AIRFLOW = Path(__file__).resolve().parents[2] / "airflow"
FORBIDDEN = ("MOTHERDUCK", "AWS_", "JOBPULSE_BUCKET", "OPENAI")


def compose(name: str) -> dict[str, Any]:
    return yaml.safe_load((AIRFLOW / name).read_text())  # type: ignore[no-any-return]


def test_base_compose_has_no_credentials_and_no_env_file() -> None:
    svc = compose("docker-compose.yml")["services"]["airflow"]
    assert "env_file" not in svc  # nothing is read from the repo .env
    env = svc["environment"]
    assert env["JOBPULSE_ENV"] == "dev"
    for name in env:
        if name == "AWS_EC2_METADATA_DISABLED":
            assert env[name] == "true"  # blocks the instance-metadata credential lookup
            continue
        assert not any(name.startswith(f) for f in FORBIDDEN), name
    for volume in svc["volumes"]:
        assert ".env" not in volume
        assert not volume.startswith(("~", "/Users", "..", "${HOME}")), volume  # no host mounts
    assert all(p.startswith("127.0.0.1:") for p in svc["ports"])  # not exposed on the network


def test_extract_override_only_adds_the_openai_env_file() -> None:
    svc = compose("docker-compose.extract.yml")["services"]["airflow"]
    assert svc == {"env_file": ["openai.env"]}
    assert (AIRFLOW / "openai.env.example").read_text().count("=") == 1
    assert "MOTHERDUCK" not in (AIRFLOW / "openai.env.example").read_text().replace(
        "MotherDuck", ""
    )


def test_image_build_context_excludes_env_files() -> None:
    ignore = (AIRFLOW / "Dockerfile.dockerignore").read_text().splitlines()
    assert "*" in ignore  # allow-list: everything is excluded by default
    allowed = {line[1:] for line in ignore if line.startswith("!")}
    assert not any(".env" in a or "infra" in a or "tfvars" in a for a in allowed)
    dockerfile = (AIRFLOW / "Dockerfile").read_text()
    copied = re.findall(r"^COPY\s+(?:--\S+\s+)*(\S.*?)\s+\S+$", dockerfile, flags=re.M)
    assert all(".env" not in c for c in copied)


def test_dag_runs_every_task_in_dev_with_local_storage() -> None:
    dag = (AIRFLOW / "dags" / "jobpulse_demo.py").read_text()
    commands = re.findall(r'f"\{JOBPULSE\} (\w[\w-]*)([^"]*)"', dag)
    assert {c for c, _ in commands} >= {
        "ingest",
        "load-ops",
        "load-bronze",
        "build-silver",
        "build-gold",
        "extract",
    }
    assert 'JOBPULSE = "/opt/jobpulse-venv/bin/jobpulse --env dev"' in dag
    for command, rest in commands:
        if command in {"ingest", "load-ops", "load-bronze"}:
            assert "--local-dir {STORAGE}" in rest, command
        if command != "ingest":
            assert "--db {DB}" in rest, command
    assert "S3" not in dag.replace("S3 or", "")  # no S3 client or bucket reference
    assert "--dry-run" in dag  # extract defaults to a dry run
    assert re.search(r'"real_extract": Param\(False', dag)
    assert "schedule=None" in dag  # manual runs only
