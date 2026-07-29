"""Command-line entry point for reproducible workflow benchmarks."""

from __future__ import annotations

import sys

from visa_research.benchmark.runner import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
