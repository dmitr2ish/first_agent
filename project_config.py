from __future__ import annotations

import subprocess


DATABASE_FILE = "visa_assistant_data.db"
SESSION_ID = "visa-cli-main"

MAX_HISTORY_RUNS = 4
MAX_SOURCE_PAGES = 5

MODEL_OPTIONS = {
    "temperature": 0.0,
    "num_ctx": 16384,
}

WORKFLOW_VERSION = "1.0.0"
PROMPT_VERSION = "visa-prompts-2026-07-21-v1"
SOURCE_COLLECTOR_VERSION = "official-sources-v1"

REPORT_DATABASE_FILE = "reports/experiment_results.db"
REPORT_XLSX_FILE = "reports/visa_model_comparison.xlsx"


def get_git_commit() -> str:
    """Return the current short commit hash for experiment metadata."""

    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "--short",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

        return result.stdout.strip()

    except (
        subprocess.SubprocessError,
        FileNotFoundError,
    ):
        return "unknown"
