"""Rank postings by skill overlap with a resume profile. Pure functions, no I/O.

Score = matched skills / the posting's extracted skills (how much of what the posting asks for
the resume covers). Sponsorship and visa evidence are filters applied by the caller before ranking
and never enter the score.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from jobpulse.config import ResumeMatchConfig


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
