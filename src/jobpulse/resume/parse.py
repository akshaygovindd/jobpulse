"""Read an uploaded or pasted resume and extract a small profile, deterministically.

Privacy contract (enforced by tests/unit/test_resume.py):
- bytes and text are handled in memory only; nothing is written, logged or printed;
- the returned `ResumeProfile` holds only skills (canonical names from gold.dim_skill), a few short
  title lines, and a years-of-experience figure, never the raw text.
No LLM: skills are found by looking up word n-grams, normalized with `jobpulse.skills.norm_skill`
(the function that built `dim_skill`), in the skill vocabulary.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from pypdf import PdfReader
from pypdf._configuration import apply_configuration

from jobpulse.config import ResumeConfig
from jobpulse.skills import norm_skill

_SEGMENT_SPLIT = re.compile(r"[\n\r,;|:•·●]+")
_EDGE_PUNCT = ".,;:()[]{}\"'"
# Lines carrying contact details are never kept as a "title line": an email, a URL, or a phone-like
# run of digits (7+ digits, with the usual separators).
_CONTACT = re.compile(
    r"(?i)(@|https?://|www\.|\b[\w-]+\.(?:com|net|org|io|dev|ai|co)\b|"
    r"(?:\+?\d[\s().-]?){7,}|linkedin|github\.)"
)


class ResumeError(Exception):
    """The file or text was refused. The message is safe to show to the user."""


@dataclass(frozen=True)
class ResumeProfile:
    skills: frozenset[str]  # canonical skill names present in the vocabulary
    titles: tuple[str, ...]  # short title-like lines, as written
    years: int | None  # years of experience from date ranges, else an explicit "N years of ..."
    education: tuple[str, ...] = ()  # degree labels found (PhD, Master's, ...)
    role_families: tuple[str, ...] = ()  # gold role families the resume's titles aim at


def read_resume(name: str, data: bytes, cfg: ResumeConfig) -> str:
    """Text of an uploaded PDF or plain-text file. Raises ResumeError for anything else."""
    up = cfg.upload
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in up.allowed_extensions:
        raise ResumeError(f"Only {', '.join(up.allowed_extensions)} files are accepted.")
    if len(data) > up.max_bytes:
        raise ResumeError(f"The file is larger than {up.max_bytes / 1_000_000:.0f} MB.")
    if not data:
        raise ResumeError("The file is empty.")
    if ext == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ResumeError("That file is not a PDF.")
        text = _pdf_text(data, up.max_pdf_pages, up.max_pdf_decoded_bytes)
    else:
        if b"\x00" in data:
            raise ResumeError("That file is not plain text.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ResumeError("That file is not UTF-8 text.") from None
    return check_text(text, cfg)


def check_text(text: str, cfg: ResumeConfig, *, pasted: bool = False) -> str:
    if not text.strip():
        if pasted:  # an empty box is not a scanned file
            raise ResumeError("Nothing to analyze yet. Paste your resume text or upload a file.")
        raise ResumeError("No text found. A scanned PDF (an image) cannot be read.")
    if len(text) > cfg.upload.max_text_chars:
        raise ResumeError(f"The text is longer than {cfg.upload.max_text_chars:,} characters.")
    return text


def _pdf_text(data: bytes, max_pages: int, max_decoded_bytes: int) -> str:
    # A few KB of compressed stream can expand to hundreds of MB. pypdf's limits make it stop
    # decoding at the cap (an error, not a crash), so memory stays small.
    limits: dict[str, Any] = {
        "zlib_maximum_output_length": max_decoded_bytes,
        "lzw_maximum_output_length": max_decoded_bytes,
        "run_length_maximum_output_length": max_decoded_bytes,
        "jbig2_maximum_output_length": max_decoded_bytes,
        "brotli_maximum_output_length": max_decoded_bytes,
        "array_based_stream_maximum_output_length": max_decoded_bytes,
        "image_maximum_buffer_size": max_decoded_bytes,
        "maximum_declared_stream_length": max_decoded_bytes,
    }
    try:
        with apply_configuration(**limits):
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise ResumeError("Encrypted PDFs cannot be read.")
            if len(reader.pages) > max_pages:
                raise ResumeError(f"The PDF has more than {max_pages} pages.")
            return "\n".join((page.extract_text() or "") for page in reader.pages)
    except ResumeError:
        raise
    except Exception:  # any parser failure is "could not be read"
        # Never echo the exception: its text can hold fragments of the file.
        raise ResumeError("That PDF could not be read.") from None


def extract_profile(
    text: str, vocabulary: Collection[str], cfg: ResumeConfig, today_year: int | None = None
) -> ResumeProfile:
    """Skills, titles, education and years from `text`. `vocabulary` is the skill names that may
    be recognized. `today_year` closes "Present" date ranges (the page passes the current year)."""
    text = clean_text(text)
    p = cfg.parse
    vocab = set(vocabulary) - set(p.ignore_skills)
    spans: list[tuple[int, int, int, str]] = []  # (segment, start word, end word, skill)
    for seg_no, segment in enumerate(_SEGMENT_SPLIT.split(text)):
        words = [w for w in (t.strip(_EDGE_PUNCT) for t in segment.split()) if w]
        for start in range(len(words)):
            for size in range(1, min(p.max_ngram, len(words) - start) + 1):
                window = words[start : start + size]
                name = norm_skill(" ".join(window))
                if name not in vocab:
                    continue
                required = p.case_sensitive_skills.get(name)
                if required is not None and (size != 1 or window[0] != required):
                    continue
                spans.append((seg_no, start, start + size, name))
    # A skill written only as part of a longer skill ("BI" inside "Power BI") is not a second
    # skill; the same word elsewhere, on its own, still counts.
    found = {
        name
        for seg, a, b, name in spans
        if not any(
            seg2 == seg and a2 <= a and b <= b2 and (b2 - a2) > (b - a) and name2 != name
            for seg2, a2, b2, name2 in spans
        )
    }
    titles = _titles(text, cfg)
    return ResumeProfile(
        frozenset(found),
        titles,
        _years(text, cfg, today_year),
        _education(text, cfg),
        _role_families(text, titles, cfg),
    )


def _is_contact(part: str) -> bool:
    """An email, link or phone number. A year range ("2024-2025" is eight digits) is not a phone."""
    return bool(_CONTACT.search(_RANGE.sub(" ", part)))


_TITLE_PARTS = re.compile(r"[|•·●]|\s[-\u2013\u2014]\s|\s{2,}")


def _title_candidates(line: str) -> list[str]:
    """A line with several parts ("Jane Doe | jane@x.com | Data Analyst") yields its parts, so a
    title survives next to contact details that are dropped. A line without a separator is one
    part."""
    parts = [p.strip(" \t-*•,;") for p in _TITLE_PARTS.split(line)]
    return [p for p in parts if p]


def _titles(text: str, cfg: ResumeConfig) -> tuple[str, ...]:
    p = cfg.parse
    keywords = tuple(k.lower() for k in p.title_keywords)
    seen: list[str] = []
    for line in text.splitlines():
        for part in _title_candidates(line):
            is_title = (
                0 < len(part) <= p.max_title_chars
                and any(k in part.lower() for k in keywords)
                and not _is_contact(part)
            )
            if is_title and part not in seen:
                seen.append(part)
        if len(seen) >= p.max_titles:
            break
    return tuple(seen[: p.max_titles])


def _family_of(title: str, cfg: ResumeConfig) -> str | None:
    """Role family for a title line: the longest configured phrase it contains."""
    low = title.lower()
    for phrase in sorted(cfg.parse.title_roles, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", low):
            return cfg.parse.title_roles[phrase]
    return None


def _role_families(text: str, titles: tuple[str, ...], cfg: ResumeConfig) -> tuple[str, ...]:
    """The role the resume aims at. The first line is the headline title ("Data Analyst"); when it
    names a role, that is the role. Otherwise every title line in the text counts, in order."""
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    for part in _title_candidates(first):
        if not _is_contact(part) and (family := _family_of(part, cfg)):
            return (family,)
    out: list[str] = []
    for title in titles:
        family = _family_of(title, cfg)
        if family and family not in out:
            out.append(family)
    return tuple(out)


_RANGE = re.compile(
    r"(?i)\b((?:19|20)\d\d)\s*(?:-|\u2013|\u2014|to)\s*((?:19|20)\d\d|present|current|now|today|ongoing)\b"
)
_EXPLICIT_YEARS = re.compile(
    r"(?i)\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:\w+\s+){0,2}?experience\b"
)
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


def clean_text(text: str) -> str:
    """NFKC-normalize (full-width letters, ligatures) and drop zero-width characters, so
    spellings that only look different are read as the skill they spell."""
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)


def _years(text: str, cfg: ResumeConfig, today_year: int | None = None) -> int | None:
    """Years of experience, or None when unsure. The union of the date ranges on lines that are
    not about education (so overlapping jobs are not counted twice and a degree is not counted as
    work). Without date ranges, an explicit "N years of ... experience". A bare "N years" anywhere
    ("volunteered for 12 years") is never used."""
    edu = [k.lower() for words in cfg.parse.education_keywords.values() for k in words]
    edu += ["university", "college", "school", "institute"]
    spans: list[tuple[int, int]] = []
    for line in text.splitlines():
        if any(k in line.lower() for k in edu):
            continue
        for m in _RANGE.finditer(line):
            start = int(m.group(1))
            end_raw = m.group(2).lower()
            if end_raw.isdigit():
                end = int(end_raw)
            elif today_year is not None:
                end = today_year
            else:
                continue
            if start <= end:
                spans.append((start, end))
    if spans:
        spans.sort()
        total, cur_start, cur_end = 0, spans[0][0], spans[0][1]
        for s, e in spans[1:]:
            if s > cur_end:
                total += cur_end - cur_start
                cur_start, cur_end = s, e
            else:
                cur_end = max(cur_end, e)
        total += cur_end - cur_start
        if 0 < total <= cfg.parse.max_years:
            return total
    explicit = [int(v) for v in _EXPLICIT_YEARS.findall(text)]
    plausible = [v for v in explicit if 0 < v <= cfg.parse.max_years]
    return max(plausible) if plausible else None


def _education(text: str, cfg: ResumeConfig) -> tuple[str, ...]:
    low = text.lower()
    out: list[str] = []
    for label, words in cfg.parse.education_keywords.items():
        by_word = any(re.search(rf"(?<![a-z]){re.escape(w.lower())}(?![a-z])", low) for w in words)
        by_pattern = any(
            re.search(pattern, line)
            for pattern in cfg.parse.education_patterns.get(label, [])
            for line in text.splitlines()
        )
        if by_word or by_pattern:
            out.append(label)
    return tuple(out)
