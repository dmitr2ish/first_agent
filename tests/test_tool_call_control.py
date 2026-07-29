import json
import unittest
from types import SimpleNamespace

from agno.workflow.step import Step, StepInput, StepOutput
from agno.workflow.workflow import Workflow

from visa_research.workflow import (
    EvidenceReadRequiredError,
    ResearcherReportEmptyError,
    build_researcher_step_output,
    run_researcher_with_evidence_control,
    summarize_tool_calls,
)


def tool(
        name: str,
        result: object,
        *,
        error: bool = False,
        duration: float | None = None,
        arguments: dict[str, object] | None = None,
) -> SimpleNamespace:
    metrics = (
        SimpleNamespace(duration=duration)
        if duration is not None
        else None
    )
    if arguments is None:
        arguments = {}

        if name == "read_official_source" and isinstance(result, dict):
            source_id = result.get("source_id")

            if source_id:
                arguments["source_id"] = source_id

    return SimpleNamespace(
        tool_name=name,
        tool_args=arguments,
        tool_call_error=error,
        result=(
            result
            if isinstance(result, str)
            else json.dumps(result)
        ),
        metrics=metrics,
    )


def response(content: str, *tools: object) -> SimpleNamespace:
    return SimpleNamespace(content=content, tools=list(tools))


class SequenceResearcher:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def run(self, prompt: str, **kwargs: object) -> object:
        self.calls.append((prompt, kwargs))
        return self.responses.pop(0)


class ToolCallSummaryTests(unittest.TestCase):
    def test_response_without_tool_calls(self) -> None:
        summary = summarize_tool_calls(SimpleNamespace(content="claim"))

        self.assertEqual(summary.total_count, 0)
        self.assertEqual(summary.successful_count, 0)
        self.assertEqual(summary.error_count, 0)
        self.assertFalse(summary.evidence_read_performed)
        self.assertIsNone(summary.total_duration_seconds)

    def test_list_only_is_not_evidence_read(self) -> None:
        summary = summarize_tool_calls(
            response(
                "claim",
                tool("list_official_sources", [{"source_id": "S1"}]),
            )
        )

        self.assertEqual(summary.tool_names, ("list_official_sources",))
        self.assertEqual(summary.successful_count, 1)
        self.assertFalse(summary.evidence_read_performed)

    def test_successful_search_with_matches_reads_evidence(self) -> None:
        summary = summarize_tool_calls(
            response(
                "report",
                tool(
                    "search_official_sources",
                    {"matches": [{"source_id": "S1", "excerpt": "fact"}]},
                    duration=0.25,
                ),
            )
        )

        self.assertTrue(summary.evidence_read_performed)
        self.assertEqual(summary.total_duration_seconds, 0.25)
        self.assertEqual(summary.evidence_source_ids, ("S1",))

    def test_successful_read_with_content_reads_evidence(self) -> None:
        summary = summarize_tool_calls(
            response(
                "report",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "official fact"},
                ),
            )
        )

        self.assertTrue(summary.evidence_read_performed)
        self.assertEqual(summary.successful_count, 1)
        self.assertEqual(summary.evidence_source_ids, ("S1",))

    def test_execution_error_is_counted(self) -> None:
        summary = summarize_tool_calls(
            response(
                "claim",
                tool(
                    "read_official_source",
                    "tool failed",
                    error=True,
                ),
            )
        )

        self.assertEqual(summary.error_count, 1)
        self.assertEqual(summary.successful_count, 0)
        self.assertFalse(summary.evidence_read_performed)

    def test_semantic_error_is_counted(self) -> None:
        summary = summarize_tool_calls(
            response(
                "claim",
                tool(
                    "read_official_source",
                    {"error": "Source S99 was not found."},
                ),
            )
        )

        self.assertEqual(summary.error_count, 1)
        self.assertFalse(summary.evidence_read_performed)

    def test_empty_search_results_are_not_evidence_read(self) -> None:
        summary = summarize_tool_calls(
            response(
                "claim",
                tool("search_official_sources", {"matches": []}),
            )
        )

        self.assertEqual(summary.successful_count, 1)
        self.assertFalse(summary.evidence_read_performed)


class ResearcherEvidenceControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_package = {
            "sources": [{"source_id": "S1", "content": "fact"}],
        }

    def test_successful_first_attempt_does_not_retry(self) -> None:
        researcher = SequenceResearcher(
            response(
                "report [S1]",
                tool(
                    "search_official_sources",
                    {"matches": [{"source_id": "S1", "excerpt": "fact"}]},
                ),
            )
        )

        result = run_researcher_with_evidence_control(
            researcher,
            "Research prompt",
            self.source_package,
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertTrue(result.tool_summary.evidence_read_performed)
        self.assertEqual(len(researcher.calls), 1)

    def test_missing_evidence_triggers_one_retry(self) -> None:
        researcher = SequenceResearcher(
            response(
                "unsupported claim",
                tool("list_official_sources", [{"source_id": "S1"}]),
            ),
            response(
                "report [S1]",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "fact"},
                ),
            ),
        )

        result = run_researcher_with_evidence_control(
            researcher,
            "Research prompt",
            self.source_package,
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertTrue(result.tool_summary.evidence_read_performed)
        self.assertEqual(result.tool_summary.total_count, 2)
        self.assertIn("MANDATORY RETRY", researcher.calls[1][0])
        self.assertIs(
            researcher.calls[1][1]["dependencies"]["source_package"],
            self.source_package,
        )

    def test_retry_stops_after_second_missing_evidence(self) -> None:
        researcher = SequenceResearcher(
            response("unsupported claim"),
            response(
                "still unsupported",
                tool("list_official_sources", [{"source_id": "S1"}]),
            ),
        )

        result = run_researcher_with_evidence_control(
            researcher,
            "Research prompt",
            self.source_package,
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertFalse(result.tool_summary.evidence_read_performed)
        self.assertEqual(len(researcher.calls), 2)

        with self.assertRaisesRegex(
                EvidenceReadRequiredError,
                "did not read evidence",
        ):
            build_researcher_step_output(result)

    def test_successful_read_allows_workflow_step_to_continue(self) -> None:
        researcher = SequenceResearcher(
            response(
                "verified report [S1]",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "fact"},
                ),
            )
        )

        result = run_researcher_with_evidence_control(
            researcher,
            "Research prompt",
            self.source_package,
        )
        step_output = build_researcher_step_output(result)

        self.assertTrue(step_output.success)
        self.assertEqual(step_output.content, "verified report [S1]")
        telemetry = step_output.metrics.additional_metrics[
            "workflow_telemetry"
        ]
        self.assertEqual(telemetry["agent_name"], "researcher")
        self.assertEqual(
            telemetry["tool_summary"]["tool_names"],
            ("read_official_source",),
        )

    def test_empty_report_after_evidence_is_failure(self) -> None:
        researcher = SequenceResearcher(
            response(
                "",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "fact"},
                ),
            )
        )

        result = run_researcher_with_evidence_control(
            researcher,
            "Research prompt",
            self.source_package,
        )

        with self.assertRaisesRegex(
                ResearcherReportEmptyError,
                "empty report",
        ):
            build_researcher_step_output(result)

    def test_missing_evidence_stops_real_workflow(self) -> None:
        researcher = SequenceResearcher(
            response("unsupported claim"),
            response(
                "still unsupported",
                tool("list_official_sources", [{"source_id": "S1"}]),
            ),
        )
        downstream_executed = False

        def research_executor(_: StepInput) -> StepOutput:
            result = run_researcher_with_evidence_control(
                researcher,
                "Research prompt",
                self.source_package,
            )
            return build_researcher_step_output(result)

        def downstream_executor(_: StepInput) -> StepOutput:
            nonlocal downstream_executed
            downstream_executed = True
            return StepOutput(content="must not execute")

        workflow = Workflow(
            id="missing-evidence-test-workflow",
            telemetry=False,
            steps=[
                Step(
                    name="research",
                    executor=research_executor,
                    max_retries=0,
                    on_error="fail",
                ),
                Step(
                    name="downstream",
                    executor=downstream_executor,
                ),
            ],
        )

        with self.assertRaisesRegex(
                EvidenceReadRequiredError,
                "did not read evidence",
        ):
            workflow.run(input="test", stream=False)

        self.assertFalse(downstream_executed)
        self.assertEqual(len(researcher.calls), 2)

    def test_successful_evidence_allows_real_workflow_to_continue(self) -> None:
        researcher = SequenceResearcher(
            response(
                "verified report [S1]",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "fact"},
                ),
            )
        )
        downstream_executed = False

        def research_executor(_: StepInput) -> StepOutput:
            result = run_researcher_with_evidence_control(
                researcher,
                "Research prompt",
                self.source_package,
            )
            return build_researcher_step_output(result)

        def downstream_executor(_: StepInput) -> StepOutput:
            nonlocal downstream_executed
            downstream_executed = True
            return StepOutput(content="continued", success=True)

        workflow = Workflow(
            id="successful-evidence-test-workflow",
            telemetry=False,
            steps=[
                Step(
                    name="research",
                    executor=research_executor,
                    max_retries=0,
                    on_error="fail",
                ),
                Step(
                    name="downstream",
                    executor=downstream_executor,
                ),
            ],
        )

        workflow_output = workflow.run(input="test", stream=False)

        self.assertTrue(downstream_executed)
        self.assertEqual(str(workflow_output.status), "RunStatus.completed")
        self.assertEqual(workflow_output.content, "continued")


if __name__ == "__main__":
    unittest.main()
