"""Canonical skill names: one normalization shared by the eval scorer and the gold layer."""

from __future__ import annotations

import re


def norm_skill(s: str) -> str:
    """Lower-case, drop parenthetical abbreviations and a leading "Apache", strip punctuation,
    singularize plural words. Pure and deterministic; "" means nothing usable was left."""
    s = s.lower().strip()
    s = re.sub(r"\s*\([^)]*\)", "", s)  # "natural language processing (nlp)" -> "... processing"
    s = re.sub(r"^apache\s+", "", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9+#./ ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    words = [
        w[:-1] if len(w) >= 4 and w.endswith("s") and not w.endswith(("ss", "is", "us")) else w
        for w in s.split()
    ]
    return " ".join(words)
