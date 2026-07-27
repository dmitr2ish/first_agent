from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import official_sources
from agno.workflow.workflow import Workflow

from experiment_reporter import ExperimentConfig, ExperimentReporter
from project_config import (
    MAX_SOURCE_PAGES,
    MODEL_OPTIONS,
    PROMPT_VERSION,
    REPORT_DATABASE_FILE,
    REPORT_XLSX_FILE,
    SOURCE_COLLECTOR_VERSION,
    WORKFLOW_VERSION,
    get_git_commit,
)
from visa_workflow import init_visa_workflow


DEFAULT_QUESTIONS_FILE = Path(__file__).with_name(
    "benchmark_questions.json"
)


def load_questions(path: str | Path) -> list[str]:
    """Load and validate the benchmark question list."""

    questions_path = Path(path)

    with questions_path.open(encoding="utf-8") as questions_file:
        payload: Any = json.load(questions_file)

    if (
        not isinstance(payload, list)
        or not payload
        or any(
            not isinstance(question, str) or not question.strip()
            for question in payload
        )
    ):
        raise ValueError(
            "Benchmark questions must be a non-empty JSON array "
            "of non-empty strings."
        )

    return [
        question.strip()
        for question in payload
    ]


def run_benchmark(
    *,
    workflow: Workflow,
    reporter: ExperimentReporter,
    experiment_run_id: int,
    questions: list[str],
) -> None:
    """Run all benchmark questions and persist success or failure."""

    for index, question in enumerate(questions, start=1):
        print(
            f"\n[Benchmark {index}/{len(questions)}] "
            f"{question}"
        )

        try:
            response = workflow.run(
                input=question,
                session_id=(
                    f"benchmark-run-{experiment_run_id}"
                ),
                additional_data={
                    "experiment_run_id": experiment_run_id,
                },
                stream=False,
            )

            reporter.record_workflow_result(
                experiment_run_id=experiment_run_id,
                question=question,
                workflow_response=response,
            )

        except Exception as error:
            reporter.record_failure(
                experiment_run_id=experiment_run_id,
                question=question,
                error=error,
            )

    reporter.finish_run(experiment_run_id)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the US visa workflow benchmark.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Local Ollama model name, for example qwen2.5:14b.",
    )
    parser.add_argument(
        "--questions",
        default=str(DEFAULT_QUESTIONS_FILE),
        help="Path to a JSON array of benchmark questions.",
    )
    parser.add_argument(
        "--db",
        default=REPORT_DATABASE_FILE,
        help="SQLite experiment database path.",
    )
    parser.add_argument(
        "--xlsx",
        default=REPORT_XLSX_FILE,
        help="Generated XLSX report path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    questions = load_questions(args.questions)

    reporter = ExperimentReporter(
        db_path=args.db,
        xlsx_path=args.xlsx,
    )
    experiment_run_id = reporter.start_run(
        ExperimentConfig(
            model_name=args.model,
            model_options=MODEL_OPTIONS,
            workflow_version=WORKFLOW_VERSION,
            prompt_version=PROMPT_VERSION,
            source_collector_version=SOURCE_COLLECTOR_VERSION,
            max_source_pages=MAX_SOURCE_PAGES,
            allowed_domains=list(official_sources.ALLOWED_DOMAINS),
            git_commit=get_git_commit(),
            notes=f"Benchmark: {Path(args.questions).name}",
        )
    )
    workflow = init_visa_workflow(model_name=args.model)

    run_benchmark(
        workflow=workflow,
        reporter=reporter,
        experiment_run_id=experiment_run_id,
        questions=questions,
    )

    print(
        "\n[Benchmark] Результаты сохранены в "
        f"{args.xlsx}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
