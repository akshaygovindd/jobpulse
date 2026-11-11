"""Rule-based pay-range parser used as an independent cross-check on the model's salary fields.

This is a regex heuristic, NOT ground truth. It finds dollar-style ranges ("$120,000 - $150,000",
"$55-$65/hr", "USD 120K to 150K") and single hourly amounts, and ignores figures that look like
company sizes ("$5M"), bonuses without a pay period, and ranges with no pay context. A posting
may list several ranges (location tiers); a model answer that matches ANY of them agrees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CUR = r"(?:US\$|USD|CA\$|CAD|C\$|AU\$|A\$|GBP|EUR|\$|£|€)"
NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
SEP = r"\s*(?:-|\u2013|\u2014|to|and)\s*"
RANGE = re.compile(
    rf"(?P<c1>{CUR})\s?(?P<a>{NUM})\s?(?P<ka>[kK])?{SEP}(?P<c2>{CUR})?\s?(?P<b>{NUM})\s?(?P<kb>[kK])?",
    re.IGNORECASE,
)
SINGLE = re.compile(rf"(?P<c>{CUR})\s?(?P<a>{NUM})\s?(?P<k>[kK])?(?![\d,])", re.IGNORECASE)
PERIODS: dict[str, re.Pattern[str]] = {
    "hour": re.compile(r"/\s?(?:hr|hour)\b|per\s+hour|an?\s+hour|hourly|\bhr\b", re.IGNORECASE),
    "year": re.compile(
        r"/\s?(?:yr|year)\b|per\s+(?:year|annum)|an?\s+year|annual|annually|\bp\.?a\.?\b|\byr\b",
        re.IGNORECASE,
    ),
    "month": re.compile(r"/\s?(?:mo|month)\b|per\s+month|monthly", re.IGNORECASE),
    "week": re.compile(r"/\s?(?:wk|week)\b|per\s+week|weekly", re.IGNORECASE),
    "day": re.compile(r"/\s?day\b|per\s+day|daily", re.IGNORECASE),
}
PAY_CONTEXT = re.compile(
    r"salary|compensation|\bpay\b|base|wage|\bOTE\b|target|total rewards|range|annual|remuneration",
    re.IGNORECASE,
)
SCALE_AFTER = re.compile(r"^\s?(?:m\b|mm\b|b\b|bn\b|million|billion|trillion)", re.IGNORECASE)
# Plausible amounts per pay period. Anything outside is ignored (e.g. a "$10,000 annual stipend").
PLAUSIBLE = {
    "hour": (8.0, 500.0),
    "day": (50.0, 5_000.0),
    "week": (200.0, 20_000.0),
    "month": (1_000.0, 100_000.0),
    "year": (15_000.0, 2_000_000.0),
}
WINDOW_AFTER = 70  # characters after the amount searched for a pay period
WINDOW_BEFORE = 200  # characters before the amount searched for pay context


@dataclass(frozen=True)
class PayRange:
    low: float
    high: float
    period: str | None  # None when the text gives no explicit period
    inferred_period: str | None  # guessed from the magnitude when no period is stated
    raw: str


def _amount(num: str, k: str | None) -> float:
    return float(num.replace(",", "")) * (1000 if k else 1)


def _period_after(text: str, end: int) -> str | None:
    window = text[end : end + WINDOW_AFTER]
    found = [(m.start(), p) for p, rx in PERIODS.items() if (m := rx.search(window))]
    return min(found)[1] if found else None


def _plausible(amount: float, period: str | None) -> bool:
    if period is None:
        return True
    lo, hi = PLAUSIBLE[period]
    return lo <= amount <= hi


def _infer(amount: float) -> str | None:
    if amount >= 20_000:
        return "year"
    if 8 <= amount <= 500:
        return "hour"
    return None


def parse_pay(text: str) -> list[PayRange]:
    """All plausible pay ranges in the posting text, in order of appearance."""
    out: list[PayRange] = []
    taken: list[tuple[int, int]] = []
    for m in RANGE.finditer(text):
        lo, hi = _amount(m["a"], m["ka"]), _amount(m["b"], m["kb"])
        if SCALE_AFTER.match(text[m.end() :]) or hi < lo:
            continue
        period = _period_after(text, m.end())
        if not (_plausible(lo, period) and _plausible(hi, period)):
            continue
        context = bool(PAY_CONTEXT.search(text[max(0, m.start() - WINDOW_BEFORE) : m.start()]))
        if not (period or (context and _infer(lo) is not None)):
            continue
        out.append(PayRange(lo, hi, period, None if period else _infer(lo), m.group(0).strip()))
        taken.append((m.start(), m.end()))
    for m in SINGLE.finditer(text):
        if any(s <= m.start() < e for s, e in taken) or SCALE_AFTER.match(text[m.end() :]):
            continue
        period = _period_after(text, m.end())
        if period is None:  # a lone amount without a pay period is not trusted
            continue
        amt = _amount(m["a"], m["k"])
        if not _plausible(amt, period):
            continue
        out.append(PayRange(amt, amt, period, None, m.group(0).strip()))
    return out


def close(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)


def compare(
    ranges: list[PayRange],
    mmin: float | None,
    mmax: float | None,
    mperiod: str | None,
    tol: float,
) -> str:
    """Classify one posting. Neither side is ground truth; this measures agreement only.

    no_salary_both   parser found nothing, model returned no salary
    model_only       model returned a salary the parser could not find (unverifiable, not an error)
    model_missed     parser found a range, model returned no salary
    match            model min/max equal some parsed range (a missing model bound is allowed)
    match_envelope   several ranges were parsed (e.g. location tiers) and the model reports the
                     overall envelope: lowest low to highest high
    match_period_conflict  amounts match but the explicit period disagrees
    partial          one bound equals a parsed bound, the other differs
    mismatch         parser found ranges, model's amounts match none of them
    """
    has_model = mmin is not None or mmax is not None
    if not ranges:
        return "model_only" if has_model else "no_salary_both"
    if not has_model:
        return "model_missed"
    best = "mismatch"
    if (
        len(ranges) > 1
        and close(mmin, min(r.low for r in ranges), tol)
        and close(mmax, max(r.high for r in ranges), tol)
    ):
        return "match_envelope"
    for r in ranges:
        lo_ok = mmin is None or close(mmin, r.low, tol)
        hi_ok = mmax is None or close(mmax, r.high, tol)
        if lo_ok and hi_ok:  # has_model guarantees at least one bound was actually compared
            if r.period and mperiod and r.period != mperiod:
                best = "match_period_conflict"
                continue
            return "match"
        if close(mmin, r.low, tol) or close(mmax, r.high, tol):
            best = "partial" if best == "mismatch" else best
    return best
