from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agno.metrics import RunMetrics
from agno.workflow.step import StepOutput

import benchmark_runner
from experiment_reporter import ExperimentConfig, ExperimentReporter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def one_turn_scenario(
    scenario_id: str,
) -> benchmark_runner.BenchmarkScenario:
    return benchmark_runner.BenchmarkScenario(
        scenario_id=scenario_id,
        title=scenario_id,
        scenario_type="independent",
        turns=(
            benchmark_runner.BenchmarkTurn(
                turn_id="question",
                prompt=f"Question for {scenario_id}?",
            ),
        ),
    )


def successful_response(
    *,
    run_number: int,
    session_id: str,
) -> SimpleNamespace:
    tool_call = {
        "name": "read_official_source",
        "arguments": {"source_id": "S1"},
        "attempt": 1,
        "success": True,
        "error": False,
        "duration_seconds": 0.1,
        "evidence_read": True,
        "result_preview": "official evidence",
    }
    source_package = {
        "sources": [
            {
                "source_id": "S1",
                "url": "https://www.uscis.gov/example",
                "content": "official content",
            }
        ]
    }
    citation_check = {
        "citation_count": 1,
        "used_source_ids": ["S1"],
        "invalid_source_ids": [],
    }

    def telemetry(payload: dict) -> RunMetrics:
        return RunMetrics(
            additional_metrics={
                "workflow_telemetry": payload,
            }
        )

    return SimpleNamespace(
        content="Answer [S1]",
        run_id=f"workflow-{run_number}",
        session_id=session_id,
        status="completed",
        metrics={"duration": 0.5},
        step_results=[
            StepOutput(
                step_name="build_search_query",
                content="official visa query",
                success=True,
            ),
            StepOutput(
                step_name="collect_official_sources",
                content=json.dumps(source_package),
                success=True,
            ),
            StepOutput(
                step_name="analyze_official_sources",
                content="Research [S1]",
                success=True,
                metrics=telemetry(
                    {
                        "agent_name": "researcher",
                        "tool_summary": {"calls": [tool_call]},
                    }
                ),
            ),
            StepOutput(
                step_name="check_research_citations",
                content=json.dumps(citation_check),
                success=True,
                metrics=telemetry(
                    {"citation_check": citation_check}
                ),
            ),
            StepOutput(
                step_name="validate_research",
                content="Validated [S1]",
                success=True,
                metrics=telemetry(
                    {
                        "agent_name": "validator",
                        "tool_summary": {"calls": [tool_call]},
                    }
                ),
            ),
            StepOutput(
                step_name="create_final_answer",
                content="Answer [S1]",
                success=True,
            ),
        ],
    )


class CapturingWorkflow:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return successful_response(
            run_number=len(self.calls),
            session_id=kwargs["session_id"],
        )


class BenchmarkRunnerTests(unittest.TestCase):
    def test_loads_versioned_benchmark_configuration(self) -> None:
        config = benchmark_runner.load_benchmark_config(
            PROJECT_ROOT / "benchmark_questions.json"
        )

        self.assertEqual(config.version, "2026-07-29-v2")
        self.assertEqual(len(config.scenarios), 9)
        self.assertEqual(config.question_count, 11)
        self.assertEqual(len(config.digest), 64)
        self.assertIn(
            "follow_up",
            {scenario.scenario_type for scenario in config.scenarios},
        )
        self.assertTrue(
            config.scenarios[0].turns[0].expectations
        )

    def test_rejects_invalid_follow_up_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            config_path = Path(temp_directory) / "invalid.json"
            config_path.write_text(
                json.dumps(
                    {
                        "version": "test",
                        "scenarios": [
                            {
                                "id": "broken-follow-up",
                                "title": "Broken",
                                "type": "follow_up",
                                "turns": [
                                    {
                                        "id": "only-turn",
                                        "prompt": "Only turn",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "at least two turns",
            ):
                benchmark_runner.load_benchmark_config(config_path)

    def test_generates_distinct_run_scoped_session_ids(self) -> None:
        first = benchmark_runner.build_scenario_session_id(
            run_namespace="invocation-a",
            experiment_run_id=7,
            scenario_number=1,
            scenario_id="First scenario",
        )
        second = benchmark_runner.build_scenario_session_id(
            run_namespace="invocation-a",
            experiment_run_id=7,
            scenario_number=2,
            scenario_id="Second scenario",
        )

        self.assertEqual(
            first,
            "benchmark-invocation-a-7-01-first-scenario",
        )
        self.assertEqual(
            second,
            "benchmark-invocation-a-7-02-second-scenario",
        )
        self.assertNotEqual(first, second)

    def test_namespaces_prevent_collision_across_fresh_report_databases(
        self,
    ) -> None:
        first = benchmark_runner.build_scenario_session_id(
            run_namespace="fresh-db-a",
            experiment_run_id=1,
            scenario_number=1,
            scenario_id="same",
        )
        second = benchmark_runner.build_scenario_session_id(
            run_namespace="fresh-db-b",
            experiment_run_id=1,
            scenario_number=1,
            scenario_id="same",
        )

        self.assertNotEqual(first, second)

    def test_long_namespaces_with_same_prefix_do_not_collide(self) -> None:
        common_prefix = "release-invocation-prefix-that-is-shared"
        first = benchmark_runner.build_scenario_session_id(
            run_namespace=f"{common_prefix}-model-a",
            experiment_run_id=1,
            scenario_number=1,
            scenario_id="same",
        )
        second = benchmark_runner.build_scenario_session_id(
            run_namespace=f"{common_prefix}-model-b",
            experiment_run_id=1,
            scenario_number=1,
            scenario_id="same",
        )

        self.assertNotEqual(first, second)

    def test_isolates_two_independent_scenarios(self) -> None:
        workflow = CapturingWorkflow()
        reporter = Mock()
        config = benchmark_runner.BenchmarkConfig(
            version="test",
            scenarios=(
                one_turn_scenario("first"),
                one_turn_scenario("second"),
            ),
            digest="digest",
        )

        benchmark_runner.run_benchmark(
            workflow=workflow,
            reporter=reporter,
            experiment_run_id=5,
            config=config,
            run_namespace="isolation-test",
        )

        session_ids = [
            call["session_id"]
            for call in workflow.calls
        ]
        self.assertEqual(len(set(session_ids)), 2)
        reporter.finish_run.assert_called_once_with(5)

    def test_preserves_one_session_inside_follow_up(self) -> None:
        workflow = CapturingWorkflow()
        reporter = Mock()
        config = benchmark_runner.BenchmarkConfig(
            version="test",
            scenarios=(
                benchmark_runner.BenchmarkScenario(
                    scenario_id="follow-up",
                    title="Follow-up",
                    scenario_type="follow_up",
                    turns=(
                        benchmark_runner.BenchmarkTurn(
                            turn_id="base",
                            prompt="Base question",
                        ),
                        benchmark_runner.BenchmarkTurn(
                            turn_id="follow",
                            prompt="And O-1?",
                        ),
                    ),
                ),
            ),
            digest="digest",
        )

        benchmark_runner.run_benchmark(
            workflow=workflow,
            reporter=reporter,
            experiment_run_id=6,
            config=config,
            run_namespace="follow-up-test",
        )

        self.assertEqual(
            [call["session_id"] for call in workflow.calls],
            [
                "benchmark-follow-up-test-6-01-follow-up",
                "benchmark-follow-up-test-6-01-follow-up",
            ],
        )
        self.assertEqual(
            [
                call["additional_data"]["turn_number"]
                for call in workflow.calls
            ],
            [1, 2],
        )

    def test_summary_counts_release_results_returned_by_reporter(
            self,
    ) -> None:
        workflow = CapturingWorkflow()
        reporter = Mock()
        reporter.record_workflow_result.side_effect = [True, False]
        config = benchmark_runner.BenchmarkConfig(
            version="test",
            scenarios=(
                one_turn_scenario("first"),
                one_turn_scenario("second"),
            ),
            digest="digest",
        )

        summary = benchmark_runner.run_benchmark(
            workflow=workflow,
            reporter=reporter,
            experiment_run_id=9,
            config=config,
            run_namespace="release-counts",
        )

        self.assertEqual(summary.attempted, 2)
        self.assertEqual(summary.workflow_completed, 2)
        self.assertEqual(summary.workflow_failed, 0)
        self.assertEqual(summary.release_passed, 1)
        self.assertEqual(summary.release_failed, 1)
        self.assertEqual(
            benchmark_runner.benchmark_exit_code(summary),
            1,
        )

    def test_missing_reporter_release_result_fails_closed(self) -> None:
        workflow = CapturingWorkflow()
        reporter = Mock()
        reporter.record_workflow_result.return_value = None
        config = benchmark_runner.BenchmarkConfig(
            version="test",
            scenarios=(one_turn_scenario("first"),),
            digest="digest",
        )

        summary = benchmark_runner.run_benchmark(
            workflow=workflow,
            reporter=reporter,
            experiment_run_id=10,
            config=config,
            run_namespace="missing-release-result",
        )

        self.assertEqual(summary.release_passed, 0)
        self.assertEqual(summary.release_failed, 1)
        self.assertEqual(
            benchmark_runner.benchmark_exit_code(summary),
            1,
        )

    def test_empty_semantic_rubric_is_not_marked_evaluated(self) -> None:
        workflow = CapturingWorkflow()
        reporter = Mock()

        def record_result(**kwargs):
            self.assertIsNone(
                kwargs["benchmark_context"]["semantic_pass"]
            )
            return False

        reporter.record_workflow_result.side_effect = record_result
        config = benchmark_runner.BenchmarkConfig(
            version="test",
            scenarios=(
                benchmark_runner.BenchmarkScenario(
                    scenario_id="empty-rubric",
                    title="Empty rubric",
                    scenario_type="independent",
                    turns=(
                        benchmark_runner.BenchmarkTurn(
                            turn_id="question",
                            prompt="Question?",
                            expectations={
                                "required_pattern_groups": [],
                                "forbidden_patterns": [],
                            },
                        ),
                    ),
                ),
            ),
            digest="digest",
        )

        summary = benchmark_runner.run_benchmark(
            workflow=workflow,
            reporter=reporter,
            experiment_run_id=11,
            config=config,
            run_namespace="empty-rubric",
        )

        self.assertEqual(summary.release_passed, 0)
        self.assertEqual(summary.release_failed, 1)

    def test_exit_code_is_zero_only_when_every_release_check_passes(
            self,
    ) -> None:
        successful = benchmark_runner.BenchmarkExecutionSummary(
            attempted=2,
            workflow_completed=2,
            workflow_failed=0,
            release_passed=2,
            release_failed=0,
        )
        workflow_failure = benchmark_runner.BenchmarkExecutionSummary(
            attempted=2,
            workflow_completed=1,
            workflow_failed=1,
            release_passed=1,
            release_failed=1,
        )
        release_only_failure = (
            benchmark_runner.BenchmarkExecutionSummary(
                attempted=2,
                workflow_completed=2,
                workflow_failed=0,
                release_passed=1,
                release_failed=1,
            )
        )
        incomplete_counts = benchmark_runner.BenchmarkExecutionSummary(
            attempted=2,
            workflow_completed=2,
            workflow_failed=0,
            release_passed=1,
            release_failed=0,
        )

        self.assertEqual(
            benchmark_runner.benchmark_exit_code(successful),
            0,
        )
        self.assertEqual(
            benchmark_runner.benchmark_exit_code(workflow_failure),
            1,
        )
        self.assertEqual(
            benchmark_runner.benchmark_exit_code(
                release_only_failure
            ),
            1,
        )
        self.assertEqual(
            benchmark_runner.benchmark_exit_code(incomplete_counts),
            1,
        )

    def test_full_smoke_matches_questions_to_report_rows(self) -> None:
        config = benchmark_runner.load_benchmark_config(
            PROJECT_ROOT / "benchmark_questions.json"
        )
        workflow = CapturingWorkflow()

        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            db_path = root / "benchmark.db"
            reporter = ExperimentReporter(
                db_path=str(db_path),
                xlsx_path=str(root / "benchmark.xlsx"),
            )
            experiment_run_id = reporter.start_run(
                ExperimentConfig(
                    model_name="smoke-model",
                    model_options={"temperature": 0},
                    workflow_version="test-workflow",
                    prompt_version="test-prompts",
                    source_collector_version="test-sources",
                    max_source_pages=5,
                    allowed_domains=["uscis.gov"],
                    git_commit="abc1234",
                    benchmark_version=config.version,
                    benchmark_config_digest=config.digest,
                    component_versions={"python": "test"},
                )
            )

            benchmark_runner.run_benchmark(
                workflow=workflow,
                reporter=reporter,
                experiment_run_id=experiment_run_id,
                config=config,
                run_namespace="full-smoke",
            )

            with sqlite3.connect(db_path) as connection:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM interactions"
                ).fetchone()[0]
                technical_passed_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM interactions
                    WHERE benchmark_technical_pass = 1
                    """
                ).fetchone()[0]
                semantic_passed_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM interactions
                    WHERE benchmark_semantic_pass = 1
                    """
                ).fetchone()[0]
                release_passed_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM interactions
                    WHERE benchmark_release_pass = 1
                    """
                ).fetchone()[0]
                distinct_sessions = connection.execute(
                    """
                    SELECT COUNT(DISTINCT session_id)
                    FROM interactions
                    """
                ).fetchone()[0]
                follow_up_sessions = connection.execute(
                    """
                    SELECT COUNT(DISTINCT session_id), COUNT(*)
                    FROM interactions
                    WHERE benchmark_scenario_id = 'contextual-follow-ups'
                    """
                ).fetchone()

        self.assertEqual(row_count, config.question_count)
        self.assertEqual(
            technical_passed_count,
            config.question_count,
        )
        self.assertEqual(semantic_passed_count, 0)
        self.assertEqual(release_passed_count, 0)
        self.assertEqual(distinct_sessions, len(config.scenarios))
        self.assertEqual(follow_up_sessions, (1, 3))


if __name__ == "__main__":
    unittest.main()
