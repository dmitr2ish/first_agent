from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import official_sources
from agno.workflow.workflow import Workflow

from experiment_reporter import ExperimentConfig, ExperimentReporter
from project_config import (
    BENCHMARK_SCHEMA_VERSION,
    MAX_SOURCE_PAGES,
    MODEL_OPTIONS,
    PROMPT_VERSION,
    REPORT_DATABASE_FILE,
    REPORT_XLSX_FILE,
    SOURCE_COLLECTOR_VERSION,
    WORKFLOW_VERSION,
    get_component_versions,
    get_git_commit,
)
from visa_workflow import init_visa_workflow


DEFAULT_QUESTIONS_FILE = Path(__file__).with_name(
    "benchmark_questions.json"
)
SCENARIO_TYPES = {
    "independent",
    "follow_up",
}


@dataclass(frozen=True)
class BenchmarkTurn:
    """One fixed user message inside a benchmark scenario."""

    turn_id: str
    prompt: str


@dataclass(frozen=True)
class BenchmarkScenario:
    """An isolated scenario whose turns share one Workflow session."""

    scenario_id: str
    title: str
    scenario_type: str
    turns: tuple[BenchmarkTurn, ...]


@dataclass(frozen=True)
class BenchmarkConfig:
    """Versioned, model-independent benchmark definition."""

    version: str
    scenarios: tuple[BenchmarkScenario, ...]
    digest: str

    @property
    def question_count(self) -> int:
        return sum(len(scenario.turns) for scenario in self.scenarios)


def _require_non_empty_string(
    value: Any,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")

    return value.strip()


def _config_digest(payload: Any) -> str:
    normalized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def load_benchmark_config(path: str | Path) -> BenchmarkConfig:
    """Load and strictly validate a phase-7 benchmark configuration."""

    questions_path = Path(path)

    with questions_path.open(encoding="utf-8") as questions_file:
        payload: Any = json.load(questions_file)

    if not isinstance(payload, dict):
        raise ValueError(
            "Benchmark configuration must be a JSON object."
        )

    version = _require_non_empty_string(
        payload.get("version"),
        field_name="version",
    )
    raw_scenarios = payload.get("scenarios")

    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ValueError("scenarios must be a non-empty array.")

    scenarios: list[BenchmarkScenario] = []
    scenario_ids: set[str] = set()

    for scenario_index, raw_scenario in enumerate(
        raw_scenarios,
        start=1,
    ):
        if not isinstance(raw_scenario, dict):
            raise ValueError(
                f"scenarios[{scenario_index}] must be an object."
            )

        scenario_id = _require_non_empty_string(
            raw_scenario.get("id"),
            field_name=f"scenarios[{scenario_index}].id",
        )

        if scenario_id in scenario_ids:
            raise ValueError(
                f"Duplicate benchmark scenario id: {scenario_id}"
            )

        scenario_ids.add(scenario_id)
        title = _require_non_empty_string(
            raw_scenario.get("title"),
            field_name=f"scenarios[{scenario_index}].title",
        )
        scenario_type = _require_non_empty_string(
            raw_scenario.get("type"),
            field_name=f"scenarios[{scenario_index}].type",
        )

        if scenario_type not in SCENARIO_TYPES:
            raise ValueError(
                f"Unsupported scenario type: {scenario_type}"
            )

        raw_turns = raw_scenario.get("turns")

        if not isinstance(raw_turns, list) or not raw_turns:
            raise ValueError(
                f"Scenario {scenario_id} must contain turns."
            )

        if scenario_type == "independent" and len(raw_turns) != 1:
            raise ValueError(
                f"Independent scenario {scenario_id} "
                "must contain exactly one turn."
            )

        if scenario_type == "follow_up" and len(raw_turns) < 2:
            raise ValueError(
                f"Follow-up scenario {scenario_id} "
                "must contain at least two turns."
            )

        turns: list[BenchmarkTurn] = []
        turn_ids: set[str] = set()

        for turn_index, raw_turn in enumerate(raw_turns, start=1):
            if not isinstance(raw_turn, dict):
                raise ValueError(
                    f"Scenario {scenario_id} turn {turn_index} "
                    "must be an object."
                )

            turn_id = _require_non_empty_string(
                raw_turn.get("id"),
                field_name=(
                    f"scenario {scenario_id} turn {turn_index} id"
                ),
            )

            if turn_id in turn_ids:
                raise ValueError(
                    f"Duplicate turn id {turn_id} "
                    f"in scenario {scenario_id}."
                )

            turn_ids.add(turn_id)
            turns.append(
                BenchmarkTurn(
                    turn_id=turn_id,
                    prompt=_require_non_empty_string(
                        raw_turn.get("prompt"),
                        field_name=(
                            f"scenario {scenario_id} "
                            f"turn {turn_index} prompt"
                        ),
                    ),
                )
            )

        scenarios.append(
            BenchmarkScenario(
                scenario_id=scenario_id,
                title=title,
                scenario_type=scenario_type,
                turns=tuple(turns),
            )
        )

    return BenchmarkConfig(
        version=version,
        scenarios=tuple(scenarios),
        digest=_config_digest(payload),
    )


def load_questions(path: str | Path) -> list[str]:
    """Return the fixed flattened question set for compatibility."""

    config = load_benchmark_config(path)
    return [
        turn.prompt
        for scenario in config.scenarios
        for turn in scenario.turns
    ]


def build_scenario_session_id(
    *,
    experiment_run_id: int,
    scenario_number: int,
    scenario_id: str,
) -> str:
    """Build one deterministic, run-scoped session ID per scenario."""

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        scenario_id.casefold(),
    ).strip("-")

    if not slug:
        raise ValueError("scenario_id must contain letters or digits.")

    return (
        f"benchmark-{experiment_run_id}-"
        f"{scenario_number:02d}-{slug}"
    )


def _benchmark_context(
    scenario: BenchmarkScenario,
    turn: BenchmarkTurn,
    *,
    turn_number: int,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario.scenario_id,
        "scenario_type": scenario.scenario_type,
        "turn_id": turn.turn_id,
        "turn_number": turn_number,
    }


def run_benchmark(
    *,
    workflow: Workflow,
    reporter: ExperimentReporter,
    experiment_run_id: int,
    config: BenchmarkConfig,
) -> None:
    """Run isolated scenarios and preserve history only within follow-ups."""

    question_number = 0

    for scenario_number, scenario in enumerate(
        config.scenarios,
        start=1,
    ):
        session_id = build_scenario_session_id(
            experiment_run_id=experiment_run_id,
            scenario_number=scenario_number,
            scenario_id=scenario.scenario_id,
        )

        for turn_number, turn in enumerate(scenario.turns, start=1):
            question_number += 1
            context = _benchmark_context(
                scenario,
                turn,
                turn_number=turn_number,
            )
            print(
                f"\n[Benchmark {question_number}/"
                f"{config.question_count}] "
                f"{scenario.scenario_id}/{turn.turn_id}: "
                f"{turn.prompt}"
            )

            try:
                response = workflow.run(
                    input=turn.prompt,
                    session_id=session_id,
                    additional_data={
                        "experiment_run_id": experiment_run_id,
                        "benchmark_version": config.version,
                        **context,
                    },
                    stream=False,
                )

                reporter.record_workflow_result(
                    experiment_run_id=experiment_run_id,
                    question=turn.prompt,
                    workflow_response=response,
                    benchmark_context=context,
                )

            except Exception as error:
                reporter.record_failure(
                    experiment_run_id=experiment_run_id,
                    question=turn.prompt,
                    error=error,
                    session_id=session_id,
                    benchmark_context=context,
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
        help="Path to a versioned benchmark scenario JSON file.",
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
    config = load_benchmark_config(args.questions)

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
            notes=(
                f"Benchmark: {Path(args.questions).name}; "
                f"{config.question_count} questions"
            ),
            benchmark_version=(
                f"{BENCHMARK_SCHEMA_VERSION}:{config.version}"
            ),
            benchmark_config_digest=config.digest,
            component_versions=get_component_versions(),
        )
    )
    workflow = init_visa_workflow(model_name=args.model)

    run_benchmark(
        workflow=workflow,
        reporter=reporter,
        experiment_run_id=experiment_run_id,
        config=config,
    )

    print(
        "\n[Benchmark] Результаты сохранены в "
        f"{args.xlsx}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
