"""Probe every seeded employer board. Thin wrapper over `jobpulse validate-employers`."""

import sys

from jobpulse.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["validate-employers", *sys.argv[1:]]))
