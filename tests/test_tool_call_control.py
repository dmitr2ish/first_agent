import json
import unittest
from types import SimpleNamespace

from visa_workflow import (
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
) -> SimpleNamespace:
    metrics = (
        SimpleNamespace(duration=duration)
        if duration is not None
        else None
    )
    return SimpleNamespace(
        tool_name=name,
        tool_args={"example": True},
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

        step_output = build_researcher_step_output(result)
        self.assertFalse(step_output.success)
        self.assertIn("did not read evidence", step_output.content)

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


if __name__ == "__main__":
    unittest.main()
