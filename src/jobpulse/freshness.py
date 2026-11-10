"""Freshness check: hours since the newest authoritative snapshot, per source."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from jobpulse.config import Employer
from jobpulse.ingest.keys import pointer_key
from jobpulse.ingest.pointer import read_pointer
from jobpulse.ingest.storage import Storage


@dataclass(frozen=True)
class SourceFreshness:
    source_id: str
    newest_completed_at: datetime | None
    age_hours: float | None
    companies: int
    companies_with_recent_pointer: int
    stale: bool


def check_freshness(
    storage: Storage,
    employers: Sequence[Employer],
    *,
    now: datetime,
    max_age_hours: float,
    lookback_days: int,
) -> list[SourceFreshness]:
    today = now.date()
    dates = [today - timedelta(days=i) for i in range(lookback_days + 1)]
    newest_by_company: dict[tuple[str, str], datetime] = {}
    for e in employers:
        for d in dates:  # newest date first
            p = read_pointer(storage, pointer_key(e.source, e.board_id, d))
            if p is not None:
                newest_by_company[(e.source, e.board_id)] = p.completed_at
                break

    results = []
    for source_id in sorted({e.source for e in employers}):
        company_keys = [(e.source, e.board_id) for e in employers if e.source == source_id]
        times = [newest_by_company[k] for k in company_keys if k in newest_by_company]
        newest = max(times) if times else None
        age = (now - newest).total_seconds() / 3600 if newest else None
        recent = sum(1 for t in times if (now - t).total_seconds() / 3600 <= max_age_hours)
        results.append(
            SourceFreshness(
                source_id=source_id,
                newest_completed_at=newest,
                age_hours=None if age is None else round(age, 2),
                companies=len(company_keys),
                companies_with_recent_pointer=recent,
                stale=age is None or age > max_age_hours,
            )
        )
    return results


def render_markdown(results: Sequence[SourceFreshness], max_age_hours: float) -> str:
    lines = [
        f"## Freshness (stale if > {max_age_hours:g}h)",
        "",
        "| Source | Newest snapshot (UTC) | Age (h) | Companies fresh | Status |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        newest = r.newest_completed_at.isoformat() if r.newest_completed_at else "none"
        age = "n/a" if r.age_hours is None else f"{r.age_hours:.1f}"
        status = "STALE" if r.stale else "ok"
        lines.append(
            f"| {r.source_id} | {newest} | {age} | "
            f"{r.companies_with_recent_pointer}/{r.companies} | {status} |"
        )
    return "\n".join(lines) + "\n"
