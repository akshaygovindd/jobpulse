"""Fail (exit 1) if any enabled source is stale. Thin wrapper over `jobpulse check-freshness`."""

import sys

from jobpulse.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["check-freshness", *sys.argv[1:]]))
