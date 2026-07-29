from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agno.metrics import RunMetrics
from agno.workflow.step import StepOutput
from openpyxl import load_workbook

from experiment_reporter import (
    ExperimentConfig,
    ExperimentReporter,
    ReportExportWarning,
)


def telemetry_metrics(payload: dict) -> RunMetrics:
    return RunMetrics(
        additional_metrics={
            "workflow_telemetry": payload,
        }
    )


def tool_call(
        name: str,
        *,
        attempt: int,
        success: bool,
        error: bool,
        evidence_read: bool,
        result_preview: str,
) -> dict:
    return {
        "name": name,
        "arguments": {"source_id": "S1"},
        "attempt": attempt,
        "success": success,
        "error": error,
        "duration_seconds": 0.25,
        "evidence_read": evidence_read,
        "evidence_source_ids": ["S1"] if evidence_read else [],
        "result_preview": result_preview,
    }


class ExperimentReporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.db_path = self.root / "report.db"
        self.xlsx_path = self.root / "report.xlsx"

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    @staticmethod
    def config() -> ExperimentConfig:
        return ExperimentConfig(
            model_name="test-model",
            model_options={"temperature": 0},
            workflow_version="1.0.0",
            prompt_version="test-prompts",
            source_collector_version="test-sources",
            max_source_pages=5,
            allowed_domains=["uscis.gov"],
            git_commit="abc1234",
            notes="phase 6 test",
        )

    @staticmethod
    def workflow_response() -> SimpleNamespace:
        researcher_calls = [
            tool_call(
                "search_official_sources",
                attempt=1,
                success=False,
                error=True,
                evidence_read=False,
                result_preview="search failed",
            ),
            tool_call(
                "read_official_source",
                attempt=2,
                success=True,
                error=False,
                evidence_read=True,
                result_preview="x" * 700,
            ),
        ]
        validator_calls = [
            tool_call(
                "read_official_source",
                attempt=1,
                success=True,
                error=False,
                evidence_read=True,
                result_preview="validated source",
            ),
        ]
        source_package = {
            "sources": [
                {
                    "source_id": "S1",
                    "url": "https://uscis.gov/example-1",
                    "content": "first source",
                },
                {
                    "source_id": "S2",
                    "url": "https://travel.state.gov/example-2",
                    "content": "second source",
                },
            ],
        }
        citation_check = {
            "citation_count": 2,
            "used_source_ids": ["S1", "S99"],
            "invalid_source_ids": ["S99"],
            "allowed_source_ids": ["S1", "S2"],
            "has_citations": True,
        }

        return SimpleNamespace(
            content="final answer",
            run_id="workflow-run-1",
            session_id="session-1",
            status="completed",
            metrics={
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "duration": 1.5,
            },
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
                    content="research [S1]",
                    success=True,
                    metrics=telemetry_metrics(
                        {
                            "agent_name": "researcher",
                            "attempt_count": 2,
                            "tool_summary": {
                                "calls": researcher_calls,
                            },
                        }
                    ),
                ),
                StepOutput(
                    step_name="check_research_citations",
                    content=json.dumps(citation_check),
                    success=True,
                    metrics=telemetry_metrics(
                        {
                            "citation_check": {
                                **citation_check,
                                "used_source_ids": ("S1", "S99"),
                                "invalid_source_ids": ("S99",),
                            },
                        }
                    ),
                ),
                StepOutput(
                    step_name="validate_research",
                    content="validated [S1]",
                    success=True,
                    metrics=telemetry_metrics(
                        {
                            "agent_name": "validator",
                            "attempt_count": 1,
                            "tool_summary": {
                                "calls": validator_calls,
                            },
                        }
                    ),
                ),
                StepOutput(
                    step_name="create_final_answer",
                    content="final answer",
                    metrics=telemetry_metrics(
                        {
                            "final_safety_check": {
                                "blocked": False,
                                "matched_patterns": [],
                            },
                            "final_output_check": {
                                "citation_count": 1,
                                "used_source_ids": ["S1"],
                                "invalid_source_ids": [],
                                "passed": True,
                                "citations_required": True,
                                "safety_policy_pass": True,
                            },
                        }
                    ),
                    success=True,
                ),
            ],
        )

    def populated_reporter(self) -> tuple[ExperimentReporter, int]:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())
        reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Test question?",
            workflow_response=self.workflow_response(),
        )
        return reporter, experiment_run_id

    def test_records_interaction_tool_and_citation_metrics(self) -> None:
        self.populated_reporter()

        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            interaction = connection.execute(
                "SELECT * FROM interactions"
            ).fetchone()

        self.assertEqual(interaction["tool_call_count"], 3)
        self.assertEqual(
            json.loads(interaction["tool_names"]),
            [
                "search_official_sources",
                "read_official_source",
                "read_official_source",
            ],
        )
        self.assertEqual(interaction["tool_error_count"], 1)
        self.assertEqual(interaction["evidence_read_performed"], 1)
        self.assertEqual(interaction["citation_count"], 2)
        self.assertEqual(
            json.loads(interaction["used_source_ids"]),
            ["S1", "S99"],
        )
        self.assertEqual(
            json.loads(interaction["invalid_source_ids"]),
            ["S99"],
        )
        self.assertEqual(interaction["source_count"], 2)
        self.assertEqual(interaction["researcher_success"], 1)
        self.assertEqual(interaction["validator_success"], 1)
        self.assertEqual(interaction["final_status"], "completed")
        self.assertEqual(interaction["final_safety_blocked"], 0)
        self.assertEqual(interaction["final_citation_count"], 1)
        self.assertEqual(
            json.loads(interaction["final_used_source_ids"]),
            ["S1"],
        )
        self.assertEqual(
            json.loads(interaction["final_invalid_source_ids"]),
            [],
        )
        self.assertEqual(interaction["final_output_policy_pass"], 1)
        self.assertEqual(interaction["final_safety_policy_pass"], 1)
        self.assertEqual(
            json.loads(interaction["evidence_source_ids"]),
            ["S1"],
        )

    def test_records_multiple_tool_calls_and_tool_error(self) -> None:
        self.populated_reporter()

        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            calls = connection.execute(
                """
                SELECT *
                FROM tool_calls
                ORDER BY sequence_number
                """
            ).fetchall()

        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [call["agent_name"] for call in calls],
            ["researcher", "researcher", "validator"],
        )
        self.assertEqual([call["attempt"] for call in calls], [1, 2, 1])
        self.assertEqual(calls[0]["error"], 1)
        self.assertEqual(calls[0]["success"], 0)
        self.assertEqual(calls[1]["evidence_read"], 1)
        self.assertEqual(
            json.loads(calls[1]["evidence_source_ids"]),
            ["S1"],
        )
        self.assertLessEqual(len(calls[1]["result_preview"]), 240)

    def test_xlsx_is_rebuilt_from_sqlite_with_all_sheets(self) -> None:
        reporter, _ = self.populated_reporter()
        self.xlsx_path.unlink()

        reporter.export_xlsx()

        workbook = load_workbook(
            self.xlsx_path,
            read_only=False,
        )
        self.assertEqual(
            workbook.sheetnames,
            [
                "Benchmark Summary",
                "Runs",
                "Interactions",
                "Step Outputs",
                "Tool Calls",
            ],
        )
        self.assertEqual(
            workbook["Benchmark Summary"].max_row,
            2,
        )
        self.assertEqual(workbook["Runs"].max_row, 2)
        self.assertEqual(workbook["Interactions"].max_row, 2)
        self.assertEqual(workbook["Step Outputs"].max_row, 7)
        self.assertEqual(workbook["Tool Calls"].max_row, 4)

        interaction_headers = [
            cell.value
            for cell in workbook["Interactions"][1]
        ]
        tool_headers = [
            cell.value
            for cell in workbook["Tool Calls"][1]
        ]
        self.assertIn("Tool Call Count", interaction_headers)
        self.assertIn("Invalid Source IDs", interaction_headers)
        self.assertIn("Safety Blocked", interaction_headers)
        self.assertIn("Final Citation Count", interaction_headers)
        self.assertIn("Final Output Policy Pass", interaction_headers)
        self.assertIn("Final Safety Policy Pass", interaction_headers)
        self.assertIn("Evidence Source IDs", interaction_headers)
        self.assertIn("Technical Pass", interaction_headers)
        self.assertIn("Semantic Pass", interaction_headers)
        self.assertIn("Release Pass", interaction_headers)
        self.assertIn("Evidence Source IDs", tool_headers)
        self.assertIn("Result Preview", tool_headers)
        self.assertEqual(
            workbook["Benchmark Summary"]["B2"].value,
            "test-model",
        )
        self.assertEqual(
            workbook["Tool Calls"].freeze_panes,
            "A2",
        )
        self.assertEqual(
            workbook["Tool Calls"]["A1"].fill.fgColor.rgb,
            "001F4E78",
        )
        self.assertFalse(
            workbook["Tool Calls"].sheet_view.showGridLines
        )

        workbook.close()

    def test_failure_records_final_status(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())

        reporter.record_failure(
            experiment_run_id=experiment_run_id,
            question="Failed question?",
            error=RuntimeError("workflow failed"),
        )

        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT status, final_status, tool_call_count
                FROM interactions
                """
            ).fetchone()

        self.assertEqual(row, ("failed", "failed", 0))

    def test_updates_post_run_artifact_provenance(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())

        reporter.update_run_component_versions(
            experiment_run_id,
            {
                "benchmark_evidence_manifest_sha256": "abc123",
            },
        )

        with sqlite3.connect(self.db_path) as connection:
            configuration_json = connection.execute(
                """
                SELECT configuration_json
                FROM experiment_runs
                WHERE id = ?
                """,
                (experiment_run_id,),
            ).fetchone()[0]

        configuration = json.loads(configuration_json)
        self.assertEqual(
            configuration["component_versions"][
                "benchmark_evidence_manifest_sha256"
            ],
            "abc123",
        )

    def test_benchmark_criterion_records_failure_reasons(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())
        response = self.workflow_response()
        response.status = "completed"
        response.step_results = [
            step
            for step in response.step_results
            if step.step_name != "collect_official_sources"
        ]

        release_pass = reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Benchmark question?",
            workflow_response=response,
            benchmark_context={
                "scenario_id": "no-sources",
                "scenario_type": "independent",
                "turn_id": "question",
                "turn_number": 1,
            },
        )

        self.assertIs(release_pass, False)

        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    benchmark_pass,
                    benchmark_failure_reasons
                FROM interactions
                """
            ).fetchone()

        self.assertEqual(row[0], 0)
        self.assertEqual(
            json.loads(row[1]),
            [
                "no_sources",
                "source_collection_not_successful",
                "semantic_not_evaluated",
            ],
        )

    def test_benchmark_distinguishes_technical_semantic_and_release(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())

        reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Semantically checked question?",
            workflow_response=self.workflow_response(),
            benchmark_context={
                "scenario_id": "semantic-failure",
                "scenario_type": "independent",
                "turn_id": "question",
                "turn_number": 1,
                "semantic_pass": False,
                "semantic_failure_reasons": [
                    "missing_expected_fact",
                ],
            },
        )

        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    benchmark_pass,
                    benchmark_technical_pass,
                    benchmark_semantic_pass,
                    benchmark_release_pass,
                    benchmark_failure_reasons,
                    semantic_failure_reasons
                FROM interactions
                """
            ).fetchone()

        self.assertEqual(row[:4], (0, 1, 0, 0))
        self.assertEqual(
            json.loads(row[4]),
            ["semantic_check_failed"],
        )
        self.assertEqual(
            json.loads(row[5]),
            ["missing_expected_fact"],
        )

    def test_benchmark_requires_researcher_and_validator_steps(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())
        response = self.workflow_response()
        response.step_results = [
            step
            for step in response.step_results
            if step.step_name != "validate_research"
        ]

        release_pass = reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Missing validator?",
            workflow_response=response,
            benchmark_context={
                "scenario_id": "missing-validator",
                "scenario_type": "independent",
            },
        )

        self.assertIs(release_pass, False)
        with sqlite3.connect(self.db_path) as connection:
            reasons_json = connection.execute(
                """
                SELECT benchmark_failure_reasons
                FROM interactions
                """
            ).fetchone()[0]

        self.assertIn(
            "validator_not_successful",
            json.loads(reasons_json),
        )

    def test_benchmark_requires_every_workflow_step_success(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())
        response = self.workflow_response()
        query_step = next(
            step
            for step in response.step_results
            if step.step_name == "build_search_query"
        )
        query_step.success = False

        release_pass = reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Failed query step?",
            workflow_response=response,
            benchmark_context={
                "scenario_id": "failed-query",
                "scenario_type": "independent",
                "semantic_pass": True,
            },
        )

        self.assertIs(release_pass, False)
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    benchmark_technical_pass,
                    benchmark_release_pass,
                    benchmark_failure_reasons
                FROM interactions
                """
            ).fetchone()

        self.assertEqual(row[:2], (0, 0))
        self.assertIn(
            "query_builder_not_successful",
            json.loads(row[2]),
        )

    def test_final_output_telemetry_drives_technical_pass(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())
        response = self.workflow_response()
        final_step = next(
            step
            for step in response.step_results
            if step.step_name == "create_final_answer"
        )
        final_step.metrics = telemetry_metrics(
            {
                "final_output_check": {
                    "citation_count": 0,
                    "used_source_ids": [],
                    "invalid_source_ids": ["S99"],
                    "passed": False,
                    "citations_required": True,
                    "safety_policy_pass": True,
                },
            }
        )

        reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Bad final answer?",
            workflow_response=response,
            benchmark_context={
                "scenario_id": "bad-final",
                "scenario_type": "independent",
                "semantic_pass": True,
            },
        )

        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    benchmark_technical_pass,
                    benchmark_release_pass,
                    benchmark_failure_reasons
                FROM interactions
                """
            ).fetchone()

        self.assertEqual(row[:2], (0, 0))
        self.assertEqual(
            json.loads(row[2]),
            [
                "final_output_policy_failed",
                "final_invalid_source_ids",
                "no_final_citations",
            ],
        )

    def test_old_telemetry_uses_researcher_citation_fallback(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())
        response = self.workflow_response()
        final_step = next(
            step
            for step in response.step_results
            if step.step_name == "create_final_answer"
        )
        final_step.metrics = telemetry_metrics(
            {
                "final_safety_check": {
                    "blocked": False,
                },
            }
        )

        reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Legacy telemetry?",
            workflow_response=response,
            benchmark_context={
                "scenario_id": "legacy",
                "scenario_type": "independent",
            },
        )

        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    benchmark_technical_pass,
                    benchmark_failure_reasons,
                    final_output_policy_pass
                FROM interactions
                """
            ).fetchone()

        self.assertEqual(row[0], 0)
        self.assertEqual(
            json.loads(row[1]),
            [
                "invalid_source_ids",
                "semantic_not_evaluated",
                "safety_policy_not_evaluated",
            ],
        )
        self.assertIsNone(row[2])

    def test_final_citations_are_optional_when_policy_says_so(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())
        response = self.workflow_response()
        final_step = next(
            step
            for step in response.step_results
            if step.step_name == "create_final_answer"
        )
        final_step.metrics = telemetry_metrics(
            {
                "final_output_check": {
                    "citation_count": 0,
                    "used_source_ids": [],
                    "invalid_source_ids": [],
                    "passed": True,
                    "citations_required": False,
                    "safety_policy_pass": True,
                },
            }
        )

        release_pass = reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="Neutral fallback?",
            workflow_response=response,
            benchmark_context={
                "scenario_id": "no-citation-required",
                "scenario_type": "independent",
                "semantic_pass": True,
            },
        )

        self.assertIs(release_pass, True)

        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    benchmark_technical_pass,
                    benchmark_release_pass,
                    benchmark_failure_reasons
                FROM interactions
                """
            ).fetchone()

        self.assertEqual(row[:2], (1, 1))
        self.assertEqual(json.loads(row[2]), [])

    def test_release_fails_when_semantic_check_is_not_evaluated(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())

        release_pass = reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question="No semantic rubric?",
            workflow_response=self.workflow_response(),
            benchmark_context={
                "scenario_id": "missing-semantic",
                "scenario_type": "independent",
            },
        )

        self.assertIs(release_pass, False)
        with sqlite3.connect(self.db_path) as connection:
            reasons_json = connection.execute(
                """
                SELECT benchmark_failure_reasons
                FROM interactions
                """
            ).fetchone()[0]

        self.assertEqual(
            json.loads(reasons_json),
            ["semantic_not_evaluated"],
        )

    def test_xlsx_sanitizes_formula_prefixes_and_preserves_types(self) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        config = self.config()
        malicious_config = ExperimentConfig(
            **{
                **config.__dict__,
                "model_name": "=HYPERLINK(\"https://example.test\")",
                "notes": "\t@SUM(1,1)",
            }
        )
        experiment_run_id = reporter.start_run(malicious_config)
        response = self.workflow_response()
        response.content = "\u200b+CMD"
        response.run_id = "-RUN"
        response.session_id = "\x01=SESSION"

        reporter.record_workflow_result(
            experiment_run_id=experiment_run_id,
            question=" \t=1+1",
            workflow_response=response,
            error="@ERROR",
        )

        workbook = load_workbook(self.xlsx_path, data_only=False)
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    self.assertNotEqual(
                        cell.data_type,
                        "f",
                        f"{sheet.title}!{cell.coordinate}",
                    )

        interaction_headers = {
            cell.value: cell.column
            for cell in workbook["Interactions"][1]
        }
        question_cell = workbook["Interactions"].cell(
            row=2,
            column=interaction_headers["Question"],
        )
        answer_cell = workbook["Interactions"].cell(
            row=2,
            column=interaction_headers["Answer"],
        )
        self.assertTrue(question_cell.value.startswith("'"))
        self.assertTrue(answer_cell.value.startswith("'"))
        workbook.close()

        self.assertEqual(
            reporter._sanitize_xlsx_value(-7),
            -7,
        )
        self.assertIs(
            reporter._sanitize_xlsx_value(True),
            True,
        )
        for value in (
            "=1+1",
            "  +SUM(1,1)",
            "\t-CMD",
            "\u200b@IMPORT",
            "\x01=HIDDEN",
        ):
            sanitized = reporter._sanitize_xlsx_value(value)
            self.assertTrue(sanitized.startswith("'"))

    def test_export_failure_does_not_reclassify_persisted_interaction(
            self,
    ) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        experiment_run_id = reporter.start_run(self.config())

        def fail_export() -> None:
            raise PermissionError("workbook is locked")

        reporter.export_xlsx = fail_export  # type: ignore[method-assign]

        with self.assertWarns(ReportExportWarning):
            reporter.record_workflow_result(
                experiment_run_id=experiment_run_id,
                question="Persist despite XLSX failure?",
                workflow_response=self.workflow_response(),
            )

        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT status FROM interactions"
            ).fetchall()

        self.assertEqual(rows, [("completed",)])
        self.assertIn("PermissionError", reporter.last_export_error or "")

    def test_migrates_existing_phase_5_database(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE experiment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_number INTEGER NOT NULL UNIQUE,
                    started_at_utc TEXT NOT NULL,
                    finished_at_utc TEXT,
                    model_name TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_run_id INTEGER NOT NULL,
                    question_number INTEGER NOT NULL,
                    workflow_run_id TEXT,
                    session_id TEXT,
                    created_at_utc TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_seconds REAL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    time_to_first_token REAL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    step_outputs_json TEXT NOT NULL DEFAULT '{}',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    benchmark_pass INTEGER,
                    error TEXT,
                    UNIQUE (experiment_run_id, question_number)
                );

                CREATE TABLE tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    interaction_id INTEGER NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    step_name TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    success INTEGER NOT NULL,
                    error INTEGER NOT NULL,
                    duration_seconds REAL,
                    evidence_read INTEGER NOT NULL,
                    result_preview TEXT NOT NULL DEFAULT '',
                    UNIQUE (interaction_id, sequence_number)
                );

                INSERT INTO experiment_runs (
                    run_number,
                    started_at_utc,
                    model_name,
                    configuration_json
                )
                VALUES (1, '2026-07-26T00:00:00+00:00', 'old', '{}');

                INSERT INTO interactions (
                    experiment_run_id,
                    question_number,
                    created_at_utc,
                    question,
                    answer,
                    status,
                    benchmark_pass
                )
                VALUES (
                    1,
                    1,
                    '2026-07-26T00:00:00+00:00',
                    'old question',
                    'old answer',
                    'completed',
                    1
                );
                """
            )

        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        reporter.export_xlsx()

        with sqlite3.connect(self.db_path) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(interactions)"
                )
            }
            migrated = connection.execute(
                """
                SELECT
                    tool_call_count,
                    tool_names,
                    final_status,
                    benchmark_pass,
                    benchmark_technical_pass,
                    benchmark_release_pass,
                    benchmark_failure_reasons
                FROM interactions
                """
            ).fetchone()
            tool_call_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(tool_calls)"
                )
            }
            tool_calls_table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'tool_calls'
                """
            ).fetchone()

        self.assertTrue(
            {
                "tool_call_count",
                "citation_count",
                "validator_success",
                "final_status",
                "final_safety_blocked",
                "final_citation_count",
                "final_used_source_ids",
                "final_invalid_source_ids",
                "final_output_policy_pass",
                "evidence_source_ids",
                "benchmark_pass",
                "benchmark_technical_pass",
                "benchmark_semantic_pass",
                "benchmark_release_pass",
                "semantic_failure_reasons",
            }.issubset(columns)
        )
        self.assertEqual(
            migrated[:6],
            (0, "[]", "unknown", 0, 1, 0),
        )
        self.assertEqual(
            json.loads(migrated[6]),
            [
                "semantic_not_evaluated",
                "safety_policy_not_evaluated",
            ],
        )
        self.assertIn("evidence_source_ids", tool_call_columns)
        self.assertEqual(tool_calls_table, ("tool_calls",))

        workbook = load_workbook(self.xlsx_path, read_only=False)
        interaction_headers = [
            cell.value
            for cell in workbook["Interactions"][1]
        ]
        self.assertIn("Final Citation Count", interaction_headers)
        self.assertIn("Release Pass", interaction_headers)
        workbook.close()

    def test_repairs_legacy_release_pass_without_semantic_evidence(
            self,
    ) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        run_id = reporter.start_run(self.config())

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO interactions (
                    experiment_run_id,
                    question_number,
                    created_at_utc,
                    question,
                    answer,
                    status,
                    benchmark_pass,
                    benchmark_technical_pass,
                    benchmark_semantic_pass,
                    benchmark_release_pass
                )
                VALUES (?, 1, ?, ?, ?, ?, 1, 1, NULL, 1)
                """,
                (
                    run_id,
                    "2026-07-29T00:00:00+00:00",
                    "legacy question",
                    "legacy answer",
                    "completed",
                ),
            )

        ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )

        with sqlite3.connect(self.db_path) as connection:
            migrated = connection.execute(
                """
                SELECT
                    benchmark_pass,
                    benchmark_technical_pass,
                    benchmark_semantic_pass,
                    benchmark_release_pass,
                    benchmark_failure_reasons
                FROM interactions
                WHERE experiment_run_id = ?
                """,
                (run_id,),
            ).fetchone()

        self.assertEqual(migrated[:4], (0, 1, None, 0))
        self.assertEqual(
            json.loads(migrated[4]),
            [
                "semantic_not_evaluated",
                "safety_policy_not_evaluated",
            ],
        )

    def test_repairs_release_pass_without_persisted_safety_evidence(
            self,
    ) -> None:
        reporter = ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )
        run_id = reporter.start_run(self.config())
        valid_step_outputs = {
            "create_final_answer": {
                "metrics": {
                    "additional_metrics": {
                        "workflow_telemetry": {
                            "final_output_check": {
                                "safety_policy_pass": True,
                            }
                        }
                    }
                }
            }
        }

        with sqlite3.connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT INTO interactions (
                    experiment_run_id,
                    question_number,
                    created_at_utc,
                    question,
                    answer,
                    status,
                    step_outputs_json,
                    benchmark_pass,
                    benchmark_technical_pass,
                    benchmark_semantic_pass,
                    benchmark_release_pass
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 1)
                """,
                [
                    (
                        run_id,
                        1,
                        "2026-07-29T00:00:00+00:00",
                        "missing safety",
                        "answer",
                        "completed",
                        "{}",
                    ),
                    (
                        run_id,
                        2,
                        "2026-07-29T00:00:01+00:00",
                        "verified safety",
                        "answer",
                        "completed",
                        json.dumps(valid_step_outputs),
                    ),
                ],
            )

        ExperimentReporter(
            db_path=str(self.db_path),
            xlsx_path=str(self.xlsx_path),
        )

        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    benchmark_pass,
                    benchmark_release_pass,
                    final_safety_policy_pass,
                    benchmark_failure_reasons
                FROM interactions
                ORDER BY question_number
                """
            ).fetchall()

        self.assertEqual(rows[0][:3], (0, 0, None))
        self.assertIn(
            "safety_policy_not_evaluated",
            json.loads(rows[0][3]),
        )
        self.assertEqual(rows[1][:3], (1, 1, 1))
        self.assertEqual(json.loads(rows[1][3]), [])


if __name__ == "__main__":
    unittest.main()
