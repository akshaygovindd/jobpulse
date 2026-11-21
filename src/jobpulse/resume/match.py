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
    score: float  # skills matched / skills asked for: the shown "N% match"
    matched: tuple[str, ...]
    missing: tuple[str, ...]


def canonical_skills(skills: Iterable[str], groups: Iterable[Iterable[str]]) -> frozenset[str]:
    """`skills` with every synonym replaced by the first name of its group, so that "dashboard"
    and "dashboarding" are one skill on both sides of the match."""
    head: dict[str, str] = {}
    for group in groups:
        names = list(group)
        for name in names:
            head[name] = names[0]
    return frozenset(head.get(s, s) for s in skills)


def rank_postings(
    resume_skills: Collection[str],
    postings: Iterable[PostingSkills],
    cfg: ResumeMatchConfig,
    role_families: Collection[str] = (),
) -> list[Match]:
    """Rank by the shown match % (whole percent, highest first). Postings tied on that percent are
    ordered by role-family fit: those in `role_families` (the role the resume's title names) first.
    There is no hidden boost: the visible order is always by visible %. Sponsorship and visa
    evidence never enter."""
    have = set(resume_skills)
    aimed = set(role_families)
    out: list[Match] = []
    for p in postings:
        if len(p.skills) < cfg.min_posting_skills:
            continue
        matched = tuple(sorted(p.skills & have))
        if len(matched) < cfg.min_matched:
            continue
        missing = tuple(sorted(p.skills - have))
        score = len(matched) / len(p.skills)
        out.append(Match(p, score, matched, missing))
    out.sort(
        key=lambda m: (
            -round(100 * m.score),  # the percent the card shows
            m.posting.info.get("role") not in aimed,  # role fit breaks ties
            -m.score,
            -len(m.matched),
            str(m.posting.info.get("company", "")),
            str(m.posting.info.get("title", "")),
            m.posting.posting_key,
        )
    )
    return out[: cfg.max_results]


def effective_visa_class(base_class: str, us_ok: bool) -> str:
    """The class a card's badge shows. A posting's own statement ("offered", "not available")
    always stands; filing history and "no filings" are about US jobs, so a card whose places are all
    abroad says so instead."""
    if base_class in ("HISTORICAL_EVIDENCE", "UNKNOWN") and not us_ok:
        return "NOT_US"
    return base_class


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
    """One row per company + title. The same role opened in several places (London, Warsaw,
    Toronto) is one posting here with a list of locations in `info["locations"]`; the first
    posting (by key, US-eligible ones first) supplies the skills and the other fields, and the
    apply link is the first one any of them has."""
    groups: dict[tuple[str, str], list[PostingSkills]] = {}
    for p in sorted(postings, key=lambda p: p.posting_key):
        key = (str(p.info.get("company", "")), str(p.info.get("title", "")))
        groups.setdefault(key, []).append(p)
    out = []
    for members in groups.values():
        # The row is shown as its US posting when there is one (H-1B evidence is about US jobs).
        # The card lists only members that name a place, so US-ness follows those places: a
        # sibling with no place (remote) must not make a Toronto-only card look US.
        located = [m for m in members if m.info.get("location")]
        members.sort(
            key=lambda m: (not m.info.get("location"), not m.info.get("us_ok", True), m.posting_key)
        )
        first = members[0]
        places = sorted({str(m.info["location"]) for m in located})
        info = {**first.info, "locations": places}
        if "us_ok" in info:
            info["us_ok"] = any(m.info["us_ok"] for m in located) if located else True
        if "apply_url" in info and not info["apply_url"]:
            info["apply_url"] = next(
                (m.info["apply_url"] for m in members if m.info["apply_url"]), ""
            )
        out.append(PostingSkills(first.posting_key, first.skills, info))
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
