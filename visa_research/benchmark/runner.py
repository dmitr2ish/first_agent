from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from agno.workflow.workflow import Workflow

from visa_research.benchmark.evaluator import (
    evaluate_semantic_expectations,
    validate_expectations_schema,
)
from visa_research.benchmark.evidence import (
    BenchmarkEvidenceCache,
    EvidenceCacheError,
    EvidenceTurn,
)
from visa_research.config import (
    BENCHMARK_SCHEMA_VERSION,
    MAX_SOURCE_PAGES,
    MODEL_OPTIONS,
    PROJECT_ROOT,
    PROMPT_VERSION,
    REPORT_DATABASE_FILE,
    REPORT_XLSX_FILE,
    SOURCE_COLLECTOR_VERSION,
    WORKFLOW_VERSION,
    get_component_versions,
    get_git_commit,
)
from visa_research.reporting import ExperimentConfig, ExperimentReporter
from visa_research.runtime import (
    DEFAULT_OLLAMA_URL,
    OllamaPreflightError,
    preflight_ollama_model,
)
from visa_research.sources import collector as official_sources
from visa_research.workflow import (
    init_visa_workflow,
    workflow_response_succeeded,
)


DEFAULT_QUESTIONS_FILE = PROJECT_ROOT / "resources" / "benchmark_questions.json"
SCENARIO_TYPES = {
    "independent",
    "follow_up",
}


@dataclass(frozen=True)
class BenchmarkTurn:
    """One fixed user message inside a benchmark scenario."""

    turn_id: str
    prompt: str
    expectations: dict[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True)
class BenchmarkExecutionSummary:
    """Lifecycle result used to produce a meaningful process exit code."""

    attempted: int
    workflow_completed: int
    workflow_failed: int
    release_passed: int
    release_failed: int


def benchmark_exit_code(summary: BenchmarkExecutionSummary) -> int:
    """Fail automation when any interaction misses workflow/release gates."""

    return int(
        summary.workflow_failed > 0
        or summary.release_failed > 0
        or summary.workflow_completed != summary.attempted
        or summary.release_passed != summary.attempted
    )


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
    """Load and strictly validate a versioned benchmark configuration."""

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
                    expectations=validate_expectations_schema(
                        raw_turn.get("expectations"),
                        field_name=(
                            f"scenario {scenario_id} "
                            f"turn {turn_index} expectations"
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


def build_run_namespace_token(run_namespace: str) -> str:
    """Normalize a namespace without losing uniqueness when it is shortened."""

    namespace_slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        run_namespace.casefold(),
    ).strip("-")

    if not namespace_slug:
        raise ValueError(
            "run_namespace must contain letters or digits."
        )

    namespace_token = namespace_slug
    if len(namespace_slug) > 24:
        namespace_token = (
            f"{namespace_slug[:24]}-"
            f"{hashlib.sha256(namespace_slug.encode('utf-8')).hexdigest()[:12]}"
        )

    return namespace_token


def build_scenario_session_id(
    *,
    run_namespace: str,
    experiment_run_id: int,
    scenario_number: int,
    scenario_id: str,
) -> str:
    """Build a globally isolated session ID for one benchmark invocation."""

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        scenario_id.casefold(),
    ).strip("-")

    if not slug:
        raise ValueError("scenario_id must contain letters or digits.")

    namespace_token = build_run_namespace_token(run_namespace)

    return (
        f"benchmark-{namespace_token}-"
        f"{experiment_run_id}-"
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


def _evidence_turn(
    scenario: BenchmarkScenario,
    turn: BenchmarkTurn,
) -> EvidenceTurn:
    return EvidenceTurn.from_prompt(
        scenario_id=scenario.scenario_id,
        turn_id=turn.turn_id,
        prompt=turn.prompt,
    )


def _expected_evidence_turns(
    config: BenchmarkConfig,
) -> tuple[EvidenceTurn, ...]:
    return tuple(
        _evidence_turn(scenario, turn)
        for scenario in config.scenarios
        for turn in scenario.turns
    )


def build_evidence_cache(
    *,
    mode: str,
    path: str | Path | None,
    config: BenchmarkConfig,
    source_collector_version: str = SOURCE_COLLECTOR_VERSION,
    workflow_version: str = WORKFLOW_VERSION,
) -> BenchmarkEvidenceCache | None:
    """Validate CLI/API evidence settings and prepare record or replay."""

    if mode == "live":
        if path is not None:
            raise EvidenceCacheError(
                "--evidence-cache requires --evidence-mode record or replay."
            )
        return None

    if mode not in {"record", "replay"}:
        raise EvidenceCacheError(
            f"Unsupported evidence mode: {mode}"
        )
    if path is None or not str(path).strip():
        raise EvidenceCacheError(
            f"--evidence-cache is required in {mode} mode."
        )

    return BenchmarkEvidenceCache(
        mode=mode,
        path=path,
        benchmark_version=config.version,
        benchmark_digest=config.digest,
        source_collector_version=source_collector_version,
        workflow_version=workflow_version,
        expected_turns=_expected_evidence_turns(config),
    )


def run_benchmark(
    *,
    workflow: Workflow,
    reporter: ExperimentReporter,
    experiment_run_id: int,
    config: BenchmarkConfig,
    run_namespace: str,
    evidence_cache: BenchmarkEvidenceCache | None = None,
) -> BenchmarkExecutionSummary:
    """Run isolated scenarios and preserve history only within follow-ups."""

    question_number = 0
    workflow_completed = 0
    workflow_failed = 0
    release_passed = 0
    release_failed = 0

    try:
        for scenario_number, scenario in enumerate(
            config.scenarios,
            start=1,
        ):
            session_id = build_scenario_session_id(
                run_namespace=run_namespace,
                experiment_run_id=experiment_run_id,
                scenario_number=scenario_number,
                scenario_id=scenario.scenario_id,
            )

            for turn_number, turn in enumerate(
                scenario.turns,
                start=1,
            ):
                question_number += 1
                evidence_turn = _evidence_turn(scenario, turn)
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

                additional_data = {
                    "experiment_run_id": experiment_run_id,
                    "benchmark_version": config.version,
                    "benchmark_run_namespace": run_namespace,
                    **context,
                }
                if (
                    evidence_cache is not None
                    and evidence_cache.mode == "replay"
                ):
                    additional_data["source_package_override"] = (
                        evidence_cache.source_package_for(
                            turn=evidence_turn
                        )
                    )

                try:
                    response = workflow.run(
                        input=turn.prompt,
                        session_id=session_id,
                        additional_data=additional_data,
                        stream=False,
                    )

                except Exception as error:
                    workflow_failed += 1
                    release_failed += 1
                    reporter.record_failure(
                        experiment_run_id=experiment_run_id,
                        question=turn.prompt,
                        error=error,
                        session_id=session_id,
                        benchmark_context=context,
                    )
                    continue

                if (
                    evidence_cache is not None
                    and evidence_cache.mode == "record"
                ):
                    evidence_cache.record_response(
                        turn=evidence_turn,
                        workflow_response=response,
                    )

                semantic_evaluation = evaluate_semantic_expectations(
                    str(getattr(response, "content", "") or ""),
                    turn.expectations,
                )
                semantic_expectations_present = bool(
                    turn.expectations.get("required_pattern_groups")
                    or turn.expectations.get("forbidden_patterns")
                )
                context = {
                    **context,
                    "semantic_pass": (
                        semantic_evaluation.passed
                        if semantic_expectations_present
                        else None
                    ),
                    "semantic_failure_reasons": list(
                        semantic_evaluation.failure_reasons
                    ),
                }

                # Keep reporting failures distinct from Workflow failures.
                # If export/storage fails after an insert, do not create a
                # duplicate "workflow_failed" interaction for the same turn.
                release_pass = reporter.record_workflow_result(
                    experiment_run_id=experiment_run_id,
                    question=turn.prompt,
                    workflow_response=response,
                    benchmark_context=context,
                )
                if release_pass is True:
                    release_passed += 1
                else:
                    # Release automation is fail-closed: a missing/unknown
                    # reporter result is not evidence that all gates passed.
                    release_failed += 1
                if workflow_response_succeeded(response):
                    workflow_completed += 1
                else:
                    workflow_failed += 1

        if evidence_cache is not None:
            evidence_cache.finalize()

    finally:
        reporter.finish_run(experiment_run_id)

    return BenchmarkExecutionSummary(
        attempted=question_number,
        workflow_completed=workflow_completed,
        workflow_failed=workflow_failed,
        release_passed=release_passed,
        release_failed=release_failed,
    )


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
    parser.add_argument(
        "--workflow-db",
        help=(
            "Agno history database. By default a namespace-specific DB is "
            "created next to --db."
        ),
    )
    parser.add_argument(
        "--run-namespace",
        help=(
            "Explicit unique namespace for scenario sessions. A UUID is used "
            "when omitted."
        ),
    )
    parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help="Ollama API base URL.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Enable verbose Agno/model logs. These logs may contain raw "
            "prompts, evidence, and unsafe model output."
        ),
    )
    parser.add_argument(
        "--evidence-mode",
        choices=("live", "record", "replay"),
        default="live",
        help=(
            "Use live retrieval, record exact source packages, or replay "
            "a frozen evidence cache."
        ),
    )
    parser.add_argument(
        "--evidence-cache",
        help=(
            "JSON evidence manifest path; required for record and replay."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    config = load_benchmark_config(args.questions)
    run_namespace = (
        str(args.run_namespace).strip()
        if args.run_namespace
        else uuid.uuid4().hex
    )

    if (
        args.evidence_mode == "live"
        and args.evidence_cache is not None
    ) or (
        args.evidence_mode != "live"
        and not args.evidence_cache
    ):
        print(
            "[Benchmark] Invalid evidence settings: "
            "--evidence-cache is required only for record/replay mode.",
            file=sys.stderr,
        )
        return 2

    try:
        model_metadata = preflight_ollama_model(
            args.model,
            base_url=args.ollama_url,
            require_tools=True,
        )
    except OllamaPreflightError as error:
        print(f"[Benchmark] Preflight failed: {error}", file=sys.stderr)
        return 2

    try:
        evidence_cache = build_evidence_cache(
            mode=args.evidence_mode,
            path=args.evidence_cache,
            config=config,
        )
    except EvidenceCacheError as error:
        print(
            f"[Benchmark] Evidence cache error: {error}",
            file=sys.stderr,
        )
        return 2

    report_db_path = Path(args.db)
    workflow_db_path = (
        Path(args.workflow_db)
        if args.workflow_db
        else report_db_path.parent
        / (
            "workflow_sessions_"
            f"{build_run_namespace_token(run_namespace)}.db"
        )
    )

    workflow = init_visa_workflow(
        model_name=args.model,
        db_file=str(workflow_db_path),
        debug_mode=args.debug,
        ollama_host=args.ollama_url,
    )
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
                f"{config.question_count} questions; "
                f"session namespace: {run_namespace}; "
                f"workflow db: {workflow_db_path}; "
                f"evidence mode: {args.evidence_mode}; "
                f"evidence cache: {args.evidence_cache or 'none'}"
            ),
            benchmark_version=(
                f"{BENCHMARK_SCHEMA_VERSION}:{config.version}"
            ),
            benchmark_config_digest=config.digest,
            component_versions={
                **get_component_versions(),
                **model_metadata.as_component_versions(),
                "benchmark_session_namespace": run_namespace,
                "benchmark_evidence_mode": args.evidence_mode,
            },
        )
    )

    try:
        summary = run_benchmark(
            workflow=workflow,
            reporter=reporter,
            experiment_run_id=experiment_run_id,
            config=config,
            run_namespace=run_namespace,
            evidence_cache=evidence_cache,
        )
    except EvidenceCacheError as error:
        print(
            f"[Benchmark] Evidence cache error: {error}",
            file=sys.stderr,
        )
        return 2

    if evidence_cache is not None:
        reporter.update_run_component_versions(
            experiment_run_id,
            {
                "benchmark_evidence_manifest_sha256": (
                    evidence_cache.manifest_sha256
                ),
            },
        )

    xlsx_export_error = reporter.last_export_error
    if xlsx_export_error:
        print(
            "\n[Benchmark] SQLite сохранён, но XLSX export failed: "
            f"{xlsx_export_error}",
            file=sys.stderr,
        )
    else:
        print(
            "\n[Benchmark] Результаты сохранены в "
            f"{args.xlsx}"
        )
    print(
        "[Benchmark] Workflow completed: "
        f"{summary.workflow_completed}/{summary.attempted}"
    )
    print(
        "[Benchmark] Release checks passed: "
        f"{summary.release_passed}/{summary.attempted}"
    )
    if evidence_cache is not None:
        print(
            "[Benchmark] Evidence manifest SHA-256: "
            f"{evidence_cache.manifest_sha256}"
        )

    exit_code = benchmark_exit_code(summary)
    if exit_code or xlsx_export_error:
        print(
            "[Benchmark] Есть workflow, release или artifact failures.",
            file=sys.stderr,
        )
        return exit_code or 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
