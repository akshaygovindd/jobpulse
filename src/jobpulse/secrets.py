"""Load named secrets from a gitignored .env file into this process's environment.

The shell never sources `.env`. Only the keys the caller names are read, existing environment
values win (so CI secrets are never overridden), and values are never logged or printed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from jobpulse.config import REPO_ROOT

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env_keys(names: Iterable[str], path: Path | None = None) -> list[str]:
    """Set each named variable from .env when it is not already set. Returns the names set."""
    wanted = set(names) - {n for n in names if os.environ.get(n)}
    env_file = path or REPO_ROOT / ".env"
    loaded: list[str] = []
    if not wanted or not env_file.is_file():
        return loaded
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        if raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m or m.group(1) not in wanted:
            continue
        value = m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            os.environ[m.group(1)] = value
            loaded.append(m.group(1))
    return loaded


def redact(text: str, *secrets: str | None) -> str:
    """Remove secret values (and anything shaped like an OpenAI key) from text."""
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return re.sub(r"sk-[A-Za-z0-9_*-]{8,}", "<redacted>", text)
