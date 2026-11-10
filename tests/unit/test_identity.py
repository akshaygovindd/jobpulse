from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest

from jobpulse.ingest.identity import (
    SnapshotDateError,
    detect_env,
    make_attempt_id,
    resolve_snapshot_date,
)

NOW = datetime(2026, 11, 10, 9, 17, tzinfo=UTC)


def test_today_accepted() -> None:
    assert resolve_snapshot_date("2026-11-10", NOW) == date(2026, 11, 10)


def test_default_is_today() -> None:
    assert resolve_snapshot_date(None, NOW) == date(2026, 11, 10)
    assert resolve_snapshot_date("", NOW) == date(2026, 11, 10)


def test_past_rejected() -> None:
    with pytest.raises(SnapshotDateError, match="past"):
        resolve_snapshot_date("2026-11-09", NOW)


def test_future_rejected() -> None:
    with pytest.raises(SnapshotDateError, match="future"):
        resolve_snapshot_date("2026-11-11", NOW)


def test_today_is_utc_not_local() -> None:
    # 23:30 at UTC-5 on Nov 9 is already Nov 10 in UTC.
    from datetime import timedelta, timezone

    local = datetime(2026, 11, 9, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert resolve_snapshot_date(None, local) == date(2026, 11, 10)
    with pytest.raises(SnapshotDateError):
        resolve_snapshot_date("2026-11-09", local)


def test_malformed_rejected() -> None:
    with pytest.raises(SnapshotDateError, match="YYYY-MM-DD"):
        resolve_snapshot_date("11/10/2026", NOW)


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_snapshot_date(None, datetime(2026, 11, 10))  # noqa: DTZ001


def test_attempt_id_gha() -> None:
    env = {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
    assert make_attempt_id(NOW, env) == "gha-123-2"


def test_attempt_id_local_unique_and_sortable() -> None:
    a = make_attempt_id(NOW, {})
    b = make_attempt_id(NOW, {})
    assert re.fullmatch(r"local-[0-9A-HJKMNP-TV-Z]{26}", a)
    assert a != b
    later = make_attempt_id(datetime(2026, 11, 10, 9, 18, tzinfo=UTC), {})
    assert later > a


def test_detect_env() -> None:
    assert detect_env({"GITHUB_ACTIONS": "true"}) == "gha"
    assert detect_env({"AIRFLOW_CTX_DAG_ID": "x"}) == "airflow"
    assert detect_env({}) == "local"
