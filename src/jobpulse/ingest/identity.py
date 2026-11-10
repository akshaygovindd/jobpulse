"""Snapshot identity: the logical `snapshot_date` and the execution `attempt_id`.

Live ingestion only ever records what a board looks like *now*, so `snapshot_date` must equal
the current UTC date. A fetch made today is never assigned to a missed day; missed days stay gaps.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime

Clock = Callable[[], datetime]

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utc_now() -> datetime:
    return datetime.now(UTC)


class SnapshotDateError(ValueError):
    pass


def resolve_snapshot_date(requested: str | date | None, now: datetime) -> date:
    """Return the snapshot date for a live run, rejecting anything but today's UTC date."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(UTC).date()
    if requested is None or requested == "":
        return today
    if isinstance(requested, str):
        try:
            parsed = date.fromisoformat(requested)
        except ValueError as e:
            raise SnapshotDateError(f"snapshot_date {requested!r} is not YYYY-MM-DD") from e
    else:
        parsed = requested
    if parsed != today:
        kind = "past" if parsed < today else "future"
        raise SnapshotDateError(
            f"snapshot_date {parsed.isoformat()} is in the {kind}; live ingestion only accepts "
            f"the current UTC date ({today.isoformat()}). Missed days stay gaps."
        )
    return parsed


def _ulid(now: datetime) -> str:
    ms = int(now.timestamp() * 1000)
    value = (ms << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def make_attempt_id(now: datetime, env: Mapping[str, str] | None = None) -> str:
    """`gha-<run_id>-<run_attempt>` inside GitHub Actions, otherwise `local-<ulid>`."""
    env = os.environ if env is None else env
    run_id = env.get("GITHUB_RUN_ID")
    if run_id:
        return f"gha-{run_id}-{env.get('GITHUB_RUN_ATTEMPT', '1')}"
    return f"local-{_ulid(now)}"


def detect_env(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") == "true":
        return "gha"
    if env.get("AIRFLOW_CTX_DAG_ID"):
        return "airflow"
    return "local"
