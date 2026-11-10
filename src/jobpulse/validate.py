"""Probe each seeded employer board and (optionally) refresh validated_at / open_job_count."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from jobpulse.config import Employer, EmployersFile
from jobpulse.connectors.base import Connector
from jobpulse.http import FetchError


@dataclass(frozen=True)
class ValidationResult:
    employer: Employer
    ok: bool
    open_job_count: int | None
    reason_code: str | None
    detail: str | None


def validate_employers(
    employers: EmployersFile, connectors: Mapping[str, Connector]
) -> list[ValidationResult]:
    results = []
    for e in employers.employers:
        try:
            count = sum(len(p.external_ids) for p in connectors[e.source].fetch_company(e.board_id))
            results.append(ValidationResult(e, True, count, None, None))
        except FetchError as err:
            results.append(ValidationResult(e, False, None, err.reason.value, err.detail))
    return results


def _q(s: str) -> str:
    return json.dumps(s)  # JSON strings are valid YAML double-quoted scalars


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def refreshed(
    employers: EmployersFile, results: list[ValidationResult], now: datetime
) -> EmployersFile:
    by_key = {(r.employer.source, r.employer.board_id): r for r in results}
    updated = []
    for e in employers.employers:
        r = by_key.get((e.source, e.board_id))
        if r and r.ok and r.open_job_count is not None:
            e = e.model_copy(update={"validated_at": now, "open_job_count": r.open_job_count})
        updated.append(e)
    return employers.model_copy(update={"employers": updated})


def dump_employers_yaml(f: EmployersFile) -> str:
    out = [
        "# JobPulse seed employers. Board ids discovered by probing public ATS APIs.",
        "# Refresh validated_at / open_job_count with: uv run jobpulse validate-employers --write",
        f"version: {f.version}",
        "",
        "employers:  # Greenhouse, Lever, Ashby",
    ]
    out += [
        f"  - {{company: {_q(e.company)}, source: {e.source}, board_id: {e.board_id}, "
        f'validated_at: "{_ts(e.validated_at)}", open_job_count: {e.open_job_count}}}'
        for e in f.employers
    ]
    out += ["", "skipped:  # no public Greenhouse/Lever/Ashby board found"]
    out += [f"  - {{company: {_q(s.company)}, reason: {_q(s.reason)}}}" for s in f.skipped]
    return "\n".join(out) + "\n"
