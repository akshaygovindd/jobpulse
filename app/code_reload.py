"""Keep the `jobpulse` package in sync with the files on disk.

Streamlit's file watcher only covers the folder of the main script (app/), so after a push that
changes only src/jobpulse/ the pages reload but `sys.modules` keeps the OLD `jobpulse.*` modules,
and a page importing a name that exists only in the new code fails with ImportError until the
app is rebooted. `refresh` compares a stamp of the source files with the last one seen by this
process and drops every `jobpulse*` module when it changed, so the next import reads the new code.
"""

from __future__ import annotations

import sys
from collections.abc import MutableMapping
from pathlib import Path


def source_stamp(src: Path) -> str:
    """A cheap fingerprint of every .py file under src/jobpulse (path, mtime, size)."""
    parts = []
    for f in sorted((src / "jobpulse").rglob("*.py")):
        st = f.stat()
        parts.append(f"{f.relative_to(src)}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


def purge_jobpulse_modules() -> int:
    stale = [m for m in sys.modules if m == "jobpulse" or m.startswith("jobpulse.")]
    for m in stale:
        del sys.modules[m]
    return len(stale)


def refresh(src: Path, state: MutableMapping[str, str]) -> bool:
    """Put `src` on sys.path once and purge stale modules. `state` must outlive reruns."""
    path = str(src)
    if path not in sys.path:
        sys.path.insert(0, path)
    stamp = source_stamp(src)
    previous = state.get("stamp")
    state["stamp"] = stamp
    if previous is not None and previous != stamp:
        purge_jobpulse_modules()
        return True
    return False
