"""Deterministic employer-name normalization and company ids (no fuzzy logic here)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def company_id_for(company: str) -> str:
    """Same slug as silver's company_id: lower-case, non-alphanumerics collapsed to '-'."""
    return re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")


def normalize_employer(name: str, legal_suffixes: Iterable[str]) -> str:
    """Lower-case ASCII words with punctuation removed and trailing legal suffixes dropped.

    "Stripe, Inc." -> "stripe"; "TOAST INC" -> "toast"; "Acme L.L.C." -> "acme". Periods are
    removed (not turned into spaces) so L.L.C. and LLC agree. Always keeps at least one word.
    """
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ").replace(".", "")
    words = _NON_ALNUM.sub(" ", text).split()
    if words and words[0] == "the" and len(words) > 1:
        words = words[1:]
    suffixes = {s.lower() for s in legal_suffixes}
    while len(words) > 1 and words[-1] in suffixes:
        words.pop()
    return " ".join(words)
