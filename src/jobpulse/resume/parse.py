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
from collections.abc import Collection
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from pypdf import PdfReader
from pypdf._configuration import apply_configuration

from jobpulse.config import ResumeConfig
from jobpulse.skills import norm_skill

_SEGMENT_SPLIT = re.compile(r"[\n\r,;|:•·●]+")
_YEARS = re.compile(r"(?i)\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b")
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
    years: int | None  # largest "N years" figure found, if plausible


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


def check_text(text: str, cfg: ResumeConfig) -> str:
    if not text.strip():
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


def extract_profile(text: str, vocabulary: Collection[str], cfg: ResumeConfig) -> ResumeProfile:
    """Skills, titles and years from `text`. `vocabulary` is gold.dim_skill.canonical_name."""
    p = cfg.parse
    vocab = set(vocabulary) - set(p.ignore_skills)
    found: set[str] = set()
    for segment in _SEGMENT_SPLIT.split(text):
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
                found.add(name)
    return ResumeProfile(frozenset(found), _titles(text, cfg), _years(text, cfg))


def _titles(text: str, cfg: ResumeConfig) -> tuple[str, ...]:
    p = cfg.parse
    keywords = tuple(k.lower() for k in p.title_keywords)
    seen: list[str] = []
    for line in text.splitlines():
        line = line.strip(" \t-*•")
        is_title = (
            0 < len(line) <= p.max_title_chars
            and any(k in line.lower() for k in keywords)
            and not _CONTACT.search(line)
        )
        if is_title and line not in seen:
            seen.append(line)
        if len(seen) >= p.max_titles:
            break
    return tuple(seen)


def _years(text: str, cfg: ResumeConfig) -> int | None:
    values = [int(m) for m in _YEARS.findall(text)]
    plausible = [v for v in values if 0 < v <= cfg.parse.max_years]
    return max(plausible) if plausible else None
