"""Rank postings by skill overlap with a resume profile. Pure functions, no I/O.

Score = matched skills / the posting's extracted skills (how much of what the posting asks for
the resume covers). Sponsorship and visa evidence are filters applied by the caller before ranking
and never enter the score.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from jobpulse.config import ResumeLevelsConfig, ResumeMatchConfig


@dataclass(frozen=True)
class PostingSkills:
    posting_key: str
    skills: frozenset[str]
    info: dict[str, Any] = field(default_factory=dict)  # display fields, passed through


@dataclass(frozen=True)
class Match:
    posting: PostingSkills
    score: float
    matched: tuple[str, ...]
    missing: tuple[str, ...]


def rank_postings(
    resume_skills: Collection[str], postings: Iterable[PostingSkills], cfg: ResumeMatchConfig
) -> list[Match]:
    have = set(resume_skills)
    out: list[Match] = []
    for p in postings:
        if len(p.skills) < cfg.min_posting_skills:
            continue
        matched = tuple(sorted(p.skills & have))
        if len(matched) < cfg.min_matched:
            continue
        missing = tuple(sorted(p.skills - have))
        out.append(Match(p, len(matched) / len(p.skills), matched, missing))
    out.sort(
        key=lambda m: (
            -m.score,
            -len(m.matched),
            str(m.posting.info.get("company", "")),
            str(m.posting.info.get("title", "")),
            m.posting.posting_key,
        )
    )
    return out[: cfg.max_results]


def level_group(seniority: str | None, cfg: ResumeLevelsConfig) -> str | None:
    """The experience-level group a gold `seniority` value belongs to."""
    for name, values in cfg.groups.items():
        if seniority in values:
            return name
    return None


def default_levels(years: int | None, titles: Iterable[str], cfg: ResumeLevelsConfig) -> list[str]:
    """Level groups pre-selected for a resume: entry levels for up to `entry_max_years` years or an
    intern title, senior levels from `senior_min_years`, else none (no filter). Only a default:
    the user can change it, and the level never enters the score."""
    if any("intern" in t.lower() for t in titles) or (
        years is not None and years <= cfg.entry_max_years
    ):
        return [cfg.entry_group, cfg.mid_group]
    if years is None:
        return []
    if years >= cfg.senior_min_years:
        return list(cfg.senior_groups)
    return [cfg.mid_group, cfg.senior_groups[0]]


def collapse_locations(postings: Iterable[PostingSkills]) -> list[PostingSkills]:
    """One row per company + title + skill set. The same role opened in several places (London,
    Warsaw, Toronto) is one posting here with a list of locations in `info["locations"]`; the
    first posting (by key) supplies the apply link and the other fields."""
    groups: dict[tuple[str, str, frozenset[str]], list[PostingSkills]] = {}
    for p in sorted(postings, key=lambda p: p.posting_key):
        key = (str(p.info.get("company", "")), str(p.info.get("title", "")), p.skills)
        groups.setdefault(key, []).append(p)
    out = []
    for members in groups.values():
        first = members[0]
        places = sorted({str(m.info["location"]) for m in members if m.info.get("location")})
        out.append(
            PostingSkills(first.posting_key, first.skills, {**first.info, "locations": places})
        )
    return out


def skills_to_learn(
    matches: Iterable[Match], top_n: int, limit: int, exclude: Collection[str] = ()
) -> list[tuple[str, int, int]]:
    """Skills the best `top_n` matches ask for that the resume lacks: (skill, in how many of them,
    how many were looked at), most common first (ties by name). `exclude` is the stop-list of
    words that are not skills."""
    best = list(matches)[:top_n]
    counts: dict[str, int] = {}
    for m in best:
        for skill in m.missing:
            if skill in exclude:
                continue
            counts[skill] = counts.get(skill, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [(skill, n, len(best)) for skill, n in ranked]
