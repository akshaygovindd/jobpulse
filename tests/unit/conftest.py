"""Shared fixtures for the extraction and evaluation tests: a small warehouse built through the
real ingest -> bronze -> silver pipeline, and a Store on top of it."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from jobpulse.config import ExtractionConfig, load_extraction, load_pipeline
from jobpulse.extract.schema import prompt_version
from jobpulse.extract.store import Store
from jobpulse.ingest.storage import LocalStorage
from tests.unit.test_extraction import D1, DESC, TITLES
from tests.warehouse_helpers import gh_job, ingest_day, run_pipeline


@pytest.fixture
def ecfg() -> ExtractionConfig:
    return load_extraction()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    storage = LocalStorage(tmp_path / "s3")
    jobs = [gh_job(i, title=t, content=f"{DESC} #{i}") for i, t in TITLES.items()]
    ingest_day(storage, D1, "greenhouse", {"acme": jobs})
    _, silver = run_pipeline(storage, tmp_path / "wh.duckdb")
    assert silver.success, silver.failures
    return tmp_path / "wh.duckdb"


@pytest.fixture
def store(db: Path, ecfg: ExtractionConfig) -> Store:
    con = duckdb.connect(str(db))
    return Store(con, load_pipeline().warehouse, ecfg, prompt_version(ecfg.schema_version))
