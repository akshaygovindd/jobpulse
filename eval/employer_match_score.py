"""Report employer-match precision from the owner's labels.

    uv run python eval/employer_match_score.py

Reads eval/employer_match_labels.csv and eval/employer_match_manifest.json and writes
eval/reports/employer_match_report.md. This is the script behind the ROADMAP P5 box
"employer-match precision on at least 50 labeled employers is reported by script".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from employer_match import read_labels, render, score

HERE = Path(__file__).resolve().parent
LABELS = HERE / "employer_match_labels.csv"
MANIFEST = HERE / "employer_match_manifest.json"
REPORT = HERE / "reports" / "employer_match_report.md"


def main() -> int:
    if not LABELS.exists() or not MANIFEST.exists():
        print(
            "error: need eval/employer_match_labels.csv and employer_match_manifest.json",
            file=sys.stderr,
        )
        return 2
    try:
        result = score(read_labels(LABELS), json.loads(MANIFEST.read_text(encoding="utf-8")))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    text = render(result)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
