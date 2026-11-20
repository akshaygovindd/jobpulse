"""Environments: `prod` (the default) and `dev` (local demos).

Dev is isolated by construction, not by convention:
- S3 keys are written under a dev prefix that is validated against the production key roots;
- the warehouse must be a local DuckDB file (MotherDuck is refused, and its token is never read);
- the demo itself runs with `--local-dir` and no cloud credentials at all, so even a bug here could
  not reach prod.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from jobpulse.ingest.storage import Storage

ENVIRONMENTS = ("prod", "dev")
ENV_VAR = "JOBPULSE_ENV"


class EnvError(Exception):
    pass


def resolve_env(value: str | None, environ: Mapping[str, str] | None = None) -> str:
    env = (value or (os.environ if environ is None else environ).get(ENV_VAR) or "prod").lower()
    if env not in ENVIRONMENTS:
        raise EnvError(f"unknown environment {env!r}; use one of {', '.join(ENVIRONMENTS)}")
    return env


def validate_dev_prefix(prefix: str, prod_prefixes: list[str]) -> str:
    """A dev prefix is a non-empty `name/` path that is neither inside nor above a prod root."""
    p = prefix.strip()
    if not p or p in {"/", "./"}:
        raise EnvError("the dev prefix is empty: dev writes would land in the production roots")
    if not p.endswith("/") or p.startswith("/") or ".." in p.split("/"):
        raise EnvError(f"dev prefix {prefix!r} must look like 'dev/' (no leading slash, no '..')")
    for root in prod_prefixes:
        if p.startswith(root) or root.startswith(p):
            raise EnvError(f"dev prefix {prefix!r} overlaps the production prefix {root!r}")
    return p


def validate_dev_db(db: str | None) -> Path:
    """Dev uses a local DuckDB file; a MotherDuck connection string is refused."""
    if not db:
        raise EnvError("dev needs --db <local file>: it never connects to MotherDuck")
    if db.lower().startswith(("md:", "motherduck:")) or "://" in db:
        raise EnvError(f"dev refuses a remote database path ({db.split(':')[0]}:...)")
    return Path(db)


class PrefixedStorage:
    """Wraps a Storage so every key lives under `prefix`; callers use the normal key layout."""

    def __init__(self, inner: Storage, prefix: str, prod_prefixes: list[str]) -> None:
        self._inner = inner
        self.prefix = validate_dev_prefix(prefix, prod_prefixes)

    def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        content_encoding: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        self._inner.put(
            self.prefix + key,
            body,
            content_type=content_type,
            content_encoding=content_encoding,
            metadata=metadata,
        )

    def get(self, key: str) -> bytes | None:
        return self._inner.get(self.prefix + key)

    def list_keys(self, prefix: str) -> list[str]:
        n = len(self.prefix)
        return [k[n:] for k in self._inner.list_keys(self.prefix + prefix)]
