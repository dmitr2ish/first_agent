"""Command-line entry point for the interactive visa research workflow."""

from __future__ import annotations

import sys

from visa_research.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
