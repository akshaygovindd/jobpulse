from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from moto import mock_aws

from jobpulse.config import Employer, HttpConfig
from jobpulse.http import HttpClient
from jobpulse.ingest.storage import S3Storage

FIXTURES = Path(__file__).parent / "fixtures"
BUCKET = "jobpulse-test-bucket"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class TickingClock:
    """Deterministic UTC clock that advances one second per call."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current


@pytest.fixture
def clock() -> TickingClock:
    return TickingClock(datetime(2026, 11, 10, 9, 17, tzinfo=UTC))


@pytest.fixture
def http_cfg() -> HttpConfig:
    return HttpConfig(
        timeout_s=5,
        max_attempts=3,
        backoff_initial_s=0.01,
        backoff_max_s=0.05,
        retry_after_cap_s=120,
        user_agent="JobPulse-test",
    )


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_http(http_cfg: HttpConfig) -> Callable[..., tuple[HttpClient, list[float]]]:
    def _make(handler: Handler) -> tuple[HttpClient, list[float]]:
        sleeps: list[float] = []
        client = HttpClient(http_cfg, transport=httpx.MockTransport(handler), sleep=sleeps.append)
        return client, sleeps

    return _make


@pytest.fixture
def s3_storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3Storage]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3Storage(BUCKET, client=client)


def employer(source: str, board_id: str, company: str | None = None) -> Employer:
    return Employer.model_validate(
        {
            "company": company or board_id.title(),
            "source": source,
            "board_id": board_id,
            "validated_at": "2026-11-10T00:00:00Z",
            "open_job_count": 1,
        }
    )
