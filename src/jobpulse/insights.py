"""One-sentence, data-computed insights for the app ("Key insight" lines, Home cards).

Pure functions over small lists of (label, value) rows that the pages read from gold: no Streamlit,
no I/O, so every sentence is unit-tested. Each returns None when there is not enough data to say
anything true, and the page then shows nothing. Numbers in a sentence always come from the rows
passed in; nothing is hard-coded.
"""

from __future__ import annotations

from collections.abc import Sequence


def share(part: float, whole: float) -> str:
    return "n/a" if not whole else f"{100 * part / whole:.0f}%"


def _lead(rows: Sequence[tuple[str, float]]) -> tuple[str, float] | None:
    clean = [(k, v) for k, v in rows if v is not None and v == v]
    return max(clean, key=lambda r: r[1]) if clean else None


def delta_text(now: float, before: float | None, *, unit: str = "vs last week") -> str | None:
    """'+120 vs last week' / '-3 vs last week'; None when there is no history to compare with."""
    if before is None or before != before:
        return None
    change = round(now - before)
    return f"{change:+,} {unit}"


def company_insight(rows: Sequence[tuple[str, float]], total_open: float) -> str | None:
    top = _lead(rows)
    if not top or not total_open:
        return None
    name, n = top
    return (
        f"{name} has the most open postings: {n:,.0f}, {share(n, total_open)} of all "
        f"{total_open:,.0f}."
    )


def role_insight(rows: Sequence[tuple[str, float]]) -> str | None:
    top = _lead(rows)
    total = sum(v for _, v in rows)
    if not top or not total:
        return None
    return (
        f"{top[0]} is the largest role family: {top[1]:,.0f} of {total:,.0f} analyzed postings "
        f"({share(top[1], total)})."
    )


def remote_insight(rows: Sequence[tuple[str, float]], unstated: str = "Not stated") -> str | None:
    """Most common stated arrangement; postings that do not say are reported, never the winner."""
    total = sum(v for _, v in rows)
    top = _lead([(k, v) for k, v in rows if k != unstated])
    if not top or not total:
        return None
    silent = sum(v for k, v in rows if k == unstated)
    tail = f" ({share(silent, total)} do not say)" if silent else ""
    return (
        f"{top[0]} is the most common stated arrangement: {share(top[1], total)} of analyzed "
        f"postings{tail}."
    )


def city_insight(rows: Sequence[tuple[str, float]], with_city: float) -> str | None:
    top = _lead(rows)
    if not top or not with_city:
        return None
    return (
        f"{top[0]} leads with {top[1]:,.0f} open postings, {share(top[1], with_city)} of the "
        f"{with_city:,.0f} that name a single city."
    )


def daily_flow_insight(rows: Sequence[tuple[str, float]], noun: str = "new postings") -> str | None:
    """rows: (day label, count) for the days that have any. Total, number of days, busiest day.
    No per-day average: days with nothing do not appear in the rows, so it would be overstated."""
    top = _lead(rows)
    if not top or not rows:
        return None
    total = sum(v for _, v in rows)
    return (
        f"{total:,.0f} {noun} across {len(rows)} days; the busiest day was {top[0]} with "
        f"{top[1]:,.0f}."
    )


def skill_insight(rows: Sequence[tuple[str, float]], role: str) -> str | None:
    """rows: (skill, share in percent) for one role family."""
    top = _lead(rows)
    if not top:
        return None
    return (
        f"For {role}, the most requested skill is {top[0]}: it appears in {top[1]:.0f}% of "
        "postings."
    )


def salary_insight(rows: Sequence[tuple[str, float, int]]) -> str | None:
    """rows: (group label, median in $K, number of salaries). Highest median among the groups."""
    clean = [r for r in rows if r[1] == r[1]]
    if not clean:
        return None
    label, median, n = max(clean, key=lambda r: r[1])
    return f"The highest median advertised pay is {label}: ${median:,.0f}K (from {n} postings)."


def sponsors_insight(rows: Sequence[tuple[str, float]]) -> str | None:
    top = _lead(rows)
    total = sum(v for _, v in rows)
    if not top or not total:
        return None
    return (
        f"{top[0]} has the most certified filings for these roles: {top[1]:,.0f}, "
        f"{share(top[1], total)} of the {total:,.0f} shown. Filings show an employer applied, "
        "not that any posting is sponsored."
    )
