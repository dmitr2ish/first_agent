from __future__ import annotations

import hashlib
import platform
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

DATABASE_FILE = "visa_assistant_data.db"
# Deprecated compatibility constant. Runtime callers now generate an isolated
# session ID for every interactive or benchmark run.
SESSION_ID = "visa-cli-main"

MAX_HISTORY_RUNS = 4
MAX_SOURCE_PAGES = 5
DEBUG_MODE = False

MODEL_OPTIONS = {
    "temperature": 0.0,
    "num_ctx": 16384,
}

WORKFLOW_VERSION = "1.1.0"
PROMPT_VERSION = "visa-prompts-2026-07-29-v3"
SOURCE_COLLECTOR_VERSION = "official-sources-v2"
BENCHMARK_SCHEMA_VERSION = "visa-benchmark-release-v2"

REQUIRED_WORKFLOW_STEPS = (
    "build_search_query",
    "collect_official_sources",
    "analyze_official_sources",
    "check_research_citations",
    "validate_research",
    "create_final_answer",
)

REPORT_DATABASE_FILE = "reports/experiment_results.db"
REPORT_XLSX_FILE = "reports/visa_model_comparison.xlsx"


def _workspace_diff_digest() -> str | None:
    """Return a stable digest for tracked and untracked release-relevant files."""

    try:
        tracked_diff = subprocess.run(
            [
                "git",
                "diff",
                "--binary",
                "HEAD",
                "--",
            ],
            check=True,
            capture_output=True,
            timeout=10,
            cwd=PROJECT_ROOT,
        ).stdout
        untracked_result = subprocess.run(
            [
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "--full-name",
                "-z",
            ],
            check=True,
            capture_output=True,
            timeout=10,
            cwd=PROJECT_ROOT,
        )
    except (
        subprocess.SubprocessError,
        FileNotFoundError,
    ):
        return None

    untracked_paths = [
        Path(raw_path.decode("utf-8", errors="surrogateescape"))
        for raw_path in untracked_result.stdout.split(b"\0")
        if raw_path
    ]

    digest = hashlib.sha256()
    digest.update(tracked_diff)

    for path in sorted(untracked_paths, key=lambda item: str(item)):
        digest.update(b"\0path\0")
        digest.update(
            str(path).encode("utf-8", errors="surrogateescape")
        )
        digest.update(b"\0content\0")

        try:
            digest.update((PROJECT_ROOT / path).read_bytes())
        except OSError:
            digest.update(b"<unreadable>")

    if not tracked_diff and not untracked_paths:
        return None

    return digest.hexdigest()


def get_git_commit() -> str:
    """Return an exact revision marker, including a dirty-tree digest."""

    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=PROJECT_ROOT,
        )

        commit = result.stdout.strip()
        diff_digest = _workspace_diff_digest()

        if diff_digest:
            return f"{commit}-dirty-{diff_digest[:16]}"

        return commit

    except (
        subprocess.SubprocessError,
        FileNotFoundError,
    ):
        return "unknown"


def get_component_versions() -> dict[str, str]:
    """Return reproducibility metadata for benchmark runtime components."""

    component_versions = {
        "python": platform.python_version(),
    }

    for package_name in (
        "agno",
        "beautifulsoup4",
        "ddgs",
        "ollama",
        "openpyxl",
        "pypdf",
        "requests",
        "trafilatura",
    ):
        try:
            component_versions[package_name] = version(package_name)
        except PackageNotFoundError:
            component_versions[package_name] = "unknown"

    return component_versions
