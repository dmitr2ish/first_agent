from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import benchmark_runner
import project_config
import visa_cli
import visa_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTests(unittest.TestCase):
    def test_new_modules_import_without_circular_dependencies(self) -> None:
        import_orders = [
            (
                "project_config",
                "visa_workflow",
                "visa_cli",
                "benchmark_runner",
            ),
            (
                "benchmark_runner",
                "visa_cli",
                "visa_workflow",
                "project_config",
            ),
        ]

        for modules in import_orders:
            code = "; ".join(
                f"import {module_name}"
                for module_name in modules
            )
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=PROJECT_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(
                completed.returncode,
                0,
                msg=completed.stderr,
            )

    def test_versions_are_centralized_in_project_config(self) -> None:
        self.assertEqual(project_config.WORKFLOW_VERSION, "1.0.0")
        self.assertTrue(project_config.PROMPT_VERSION)
        self.assertTrue(project_config.SOURCE_COLLECTOR_VERSION)
        self.assertTrue(project_config.BENCHMARK_SCHEMA_VERSION)
        self.assertIn("python", project_config.get_component_versions())

    def test_workflow_factory_preserves_step_sequence(self) -> None:
        workflow = visa_workflow.init_visa_workflow("test-model")

        self.assertEqual(
            [step.name for step in workflow.steps],
            [
                "build_search_query",
                "collect_official_sources",
                "analyze_official_sources",
                "check_research_citations",
                "validate_research",
                "create_final_answer",
            ],
        )

    def test_cli_reaches_user_prompt_and_exits(self) -> None:
        reporter = Mock()
        reporter.start_run.return_value = 7
        reporter.get_run_number.return_value = 3
        workflow = Mock()

        with (
            patch.object(
                visa_cli,
                "choose_model",
                return_value="test-model",
            ),
            patch.object(
                visa_cli,
                "ExperimentReporter",
                return_value=reporter,
            ),
            patch.object(
                visa_cli,
                "init_visa_workflow",
                return_value=workflow,
            ),
            patch.object(
                visa_cli,
                "get_git_commit",
                return_value="abc1234",
            ),
            patch("builtins.input", return_value="/exit") as user_input,
            redirect_stdout(StringIO()),
        ):
            visa_cli.main()

        user_input.assert_called_once()
        reporter.start_run.assert_called_once()
        reporter.finish_run.assert_called_once_with(7)
        workflow.run.assert_not_called()

    def test_benchmark_help_starts_without_runtime_services(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "benchmark_runner.py"),
                "--help",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr,
        )
        self.assertIn("--model", completed.stdout)
        self.assertIn("--questions", completed.stdout)

    def test_benchmark_main_runs_loaded_questions(self) -> None:
        reporter = Mock()
        reporter.start_run.return_value = 11
        workflow = Mock()
        workflow.run.return_value = SimpleNamespace(
            content="answer",
            run_id="run-1",
            session_id="benchmark-run-11",
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            questions_path = Path(temp_directory) / "questions.json"
            questions_path.write_text(
                json.dumps(
                    {
                        "version": "test-v1",
                        "scenarios": [
                            {
                                "id": "question-one",
                                "title": "Question one",
                                "type": "independent",
                                "turns": [
                                    {
                                        "id": "turn-one",
                                        "prompt": "Question one?",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(
                    benchmark_runner,
                    "ExperimentReporter",
                    return_value=reporter,
                ),
                patch.object(
                    benchmark_runner,
                    "init_visa_workflow",
                    return_value=workflow,
                ),
                patch.object(
                    benchmark_runner,
                    "get_git_commit",
                    return_value="abc1234",
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = benchmark_runner.main(
                    [
                        "--model",
                        "test-model",
                        "--questions",
                        str(questions_path),
                    ]
                )

        self.assertEqual(exit_code, 0)
        workflow.run.assert_called_once()
        reporter.record_workflow_result.assert_called_once()
        reporter.finish_run.assert_called_once_with(11)


if __name__ == "__main__":
    unittest.main()
