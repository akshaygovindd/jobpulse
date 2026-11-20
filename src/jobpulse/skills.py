"""Canonical skill names: one normalization shared by the eval scorer and the gold layer."""

from __future__ import annotations

import re

# Words that end in "s" but are not plurals (or whose plural is the usual name), kept as written.
_KEEP = frozenset(
    [
        "kubernetes",
        "databricks",
        "pandas",
        "bias",
        "series",
        "species",
        "windows",
        "alias",
        "mlops",
        "devops",
        "gitops",
        "dataops",
        "aiops",
        "finops",
        "llmops",
        "secops",
    ]
)
# Plurals the general rule would leave alone (they end in "is") or that are better merged.
_IRREGULAR = {"apis": "api", "kpis": "kpi", "okrs": "okr", "metrics": "metric"}


def _singular(w: str) -> str:
    """One word to its singular, only when it is clearly a plural: stories -> story,
    pipelines -> pipeline, but series, bias, pandas, Kubernetes, analytics and statistics stay."""
    if w in _KEEP:
        return w
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    if len(w) < 4 or not w.endswith("s") or w.endswith(("ss", "is", "us", "ics")):
        return w
    if len(w) >= 5 and w.endswith("ies"):
        return w[:-3] + "y"
    return w[:-1]


def norm_skill(s: str) -> str:
    """Lower-case, drop parenthetical abbreviations and a leading "Apache", strip punctuation,
    singularize clear plurals (see _singular). Pure and deterministic; "" means nothing usable was
    left."""
    s = s.lower().strip()
    s = re.sub(r"\s*\([^)]*\)", "", s)  # "natural language processing (nlp)" -> "... processing"
    s = re.sub(r"^apache\s+", "", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9+#./ ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return " ".join(_singular(w) for w in s.split())
