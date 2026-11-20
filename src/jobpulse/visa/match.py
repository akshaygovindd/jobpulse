"""Match LCA employer names to our companies. Deterministic only; fuzzy results are suggestions.

Methods, strongest first: `alias` (listed in config/lca_aliases.yaml, owner-reviewed), `exact`
(the LCA name equals the company name, case-insensitive), `normalized` (equal after normalization).
Confidences come from config/lca.yaml. A name that matches more than one company is ambiguous and is
not matched at all. Similarity scoring (`suggest`) is used only to propose candidates for review.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from jobpulse.config import LcaMatchConfig
from jobpulse.visa.normalize import company_id_for, normalize_employer

_METHOD_RANK = {"alias": 0, "exact": 1, "normalized": 2}


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    norm: str


@dataclass(frozen=True)
class NameRow:
    raw: str
    norm: str
    n_cases: int


@dataclass(frozen=True)
class Match:
    company_id: str
    norm_name: str
    example_name: str
    method: str
    confidence: float
    reviewed: bool
    n_cases: int


@dataclass(frozen=True)
class Suggestion:
    company_id: str
    norm_name: str
    example_name: str
    similarity: float
    n_cases: int


@dataclass(frozen=True)
class MatchResult:
    matches: list[Match]
    ambiguous: dict[str, list[str]]  # normalized LCA name -> company ids it would match


def build_companies(names: Iterable[str], cfg: LcaMatchConfig) -> list[Company]:
    seen: dict[str, Company] = {}
    for name in names:
        cid = company_id_for(name)
        seen.setdefault(cid, Company(cid, name, normalize_employer(name, cfg.legal_suffixes)))
    return sorted(seen.values(), key=lambda c: c.company_id)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def build_matches(
    names: Sequence[NameRow],
    companies: Sequence[Company],
    aliases: dict[str, list[str]],
    cfg: LcaMatchConfig,
) -> MatchResult:
    alias_norms = {
        cid: {normalize_employer(a, cfg.legal_suffixes) for a in lst}
        for cid, lst in aliases.items()
    }
    by_norm: dict[str, dict[str, str]] = {}  # norm -> company_id -> best method
    cases: dict[str, int] = {}
    example: dict[str, tuple[int, str]] = {}
    for row in names:
        cases[row.norm] = cases.get(row.norm, 0) + row.n_cases
        if row.norm not in example or row.n_cases > example[row.norm][0]:
            example[row.norm] = (row.n_cases, row.raw)
        for c in companies:
            if row.norm in alias_norms.get(c.company_id, ()):
                method = "alias"
            elif _collapse(row.raw) == _collapse(c.name):
                method = "exact"
            elif row.norm == c.norm:
                method = "normalized"
            else:
                continue
            current = by_norm.setdefault(row.norm, {}).get(c.company_id)
            if current is None or _METHOD_RANK[method] < _METHOD_RANK[current]:
                by_norm[row.norm][c.company_id] = method
    matches: list[Match] = []
    ambiguous: dict[str, list[str]] = {}
    for norm, hits in sorted(by_norm.items()):
        if len(hits) > 1:
            ambiguous[norm] = sorted(hits)
            continue
        ((cid, method),) = hits.items()
        matches.append(
            Match(
                company_id=cid,
                norm_name=norm,
                example_name=example[norm][1],
                method=method,
                confidence=cfg.confidence[method],
                reviewed=method == "alias",
                n_cases=cases[norm],
            )
        )
    return MatchResult(matches=matches, ambiguous=ambiguous)


def suggest(
    names: Sequence[NameRow],
    companies: Sequence[Company],
    matched_norms: set[str],
    cfg: LcaMatchConfig,
) -> list[Suggestion]:
    """Near-miss candidates for human review. Never used as evidence."""
    out: dict[tuple[str, str], Suggestion] = {}
    for row in names:
        if row.norm in matched_norms:
            continue
        for c in companies:
            if not c.norm:
                continue
            words = row.norm.split()
            contains = c.norm.split() == words[: len(c.norm.split())] and len(words) > 1
            ratio = SequenceMatcher(None, c.norm, row.norm).ratio()
            sim = max(ratio, 0.85 if contains else 0.0)
            if sim >= cfg.fuzzy_suggestion_min_similarity:
                key = (c.company_id, row.norm)
                prev = out.get(key)
                if prev is None or row.n_cases > prev.n_cases:
                    out[key] = Suggestion(
                        c.company_id, row.norm, row.raw, round(sim, 3), row.n_cases
                    )
    return sorted(out.values(), key=lambda s: (s.company_id, -s.similarity, -s.n_cases))
