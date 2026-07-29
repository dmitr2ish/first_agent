import json
import unittest
from types import SimpleNamespace

from visa_workflow import (
    CitationEvidenceMismatchError,
    ValidatorEvidenceRequiredError,
    ValidatorOutputPolicyError,
    build_validator_step_output,
    check_validator_output,
    citation_check_to_json,
    check_report_citations,
    run_validator_with_tool_control,
)


def tool(
        name: str,
        result: object,
        *,
        error: bool = False,
        arguments: dict[str, object] | None = None,
) -> SimpleNamespace:
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
        metrics=None,
    )


def response(content: str, *tools: object) -> SimpleNamespace:
    return SimpleNamespace(content=content, tools=list(tools))


class SequenceValidator:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def run(self, prompt: str, **kwargs: object) -> object:
        self.calls.append((prompt, kwargs))
        return self.responses.pop(0)


class ValidatorControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_package = {
            "sources": [
                {
                    "source_id": "S1",
                    "content": "O-1A is a nonimmigrant classification.",
                },
                {
                    "source_id": "S2",
                    "content": "Second official source.",
                },
            ],
        }
        self.source_package_json = json.dumps(self.source_package)
        self.original_report = (
            "O-1A is an immigrant classification [S1]."
        )
        self.citation_json = citation_check_to_json(
            check_report_citations(
                self.original_report,
                self.source_package,
            )
        )

    def test_successful_validator_tools_are_tracked_separately(self) -> None:
        validator = SequenceValidator(
            response(
                "O-1A is a nonimmigrant classification [S1].",
                tool(
                    "check_source_ids",
                    {"valid": ["S1"], "invalid": []},
                ),
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "official evidence"},
                ),
            )
        )

        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            self.original_report,
            self.citation_json,
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.tool_summary.total_count, 2)
        self.assertEqual(
            result.tool_summary.tool_names,
            ("check_source_ids", "read_official_source"),
        )
        self.assertTrue(result.tool_summary.evidence_read_performed)
        self.assertEqual(len(validator.calls), 1)
        self.assertEqual(
            validator.calls[0][1]["dependencies"]["source_package"],
            self.source_package,
        )

    def test_check_ids_without_reading_triggers_one_retry(self) -> None:
        validator = SequenceValidator(
            response(
                "Unverified output [S1].",
                tool(
                    "check_source_ids",
                    {"valid": ["S1"], "invalid": []},
                ),
            ),
            response(
                "Verified output [S1].",
                tool(
                    "search_official_sources",
                    {
                        "matches": [
                            {"source_id": "S1", "excerpt": "evidence"},
                        ],
                    },
                ),
            ),
        )

        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            self.original_report,
            self.citation_json,
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.tool_summary.total_count, 2)
        self.assertTrue(result.tool_summary.evidence_read_performed)
        self.assertIn("MANDATORY RETRY", validator.calls[1][0])

    def test_missing_evidence_after_retry_is_fatal(self) -> None:
        validator = SequenceValidator(
            response("First unverified output."),
            response(
                "Second unverified output.",
                tool(
                    "check_source_ids",
                    {"valid": ["S1"], "invalid": []},
                ),
            ),
        )

        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            self.original_report,
            self.citation_json,
        )

        with self.assertRaisesRegex(
                ValidatorEvidenceRequiredError,
                "did not read evidence",
        ):
            build_validator_step_output(
                result,
                self.original_report,
                self.source_package,
            )

        self.assertEqual(len(validator.calls), 2)

    def test_wrong_classification_can_be_corrected(self) -> None:
        validator = SequenceValidator(
            response(
                "O-1A is a nonimmigrant classification [S1].",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "official evidence"},
                ),
            )
        )
        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            self.original_report,
            self.citation_json,
        )

        step_output = build_validator_step_output(
            result,
            self.original_report,
            self.source_package,
        )

        self.assertTrue(step_output.success)
        self.assertIn("nonimmigrant", step_output.content)
        self.assertNotIn("is an immigrant", step_output.content)
        telemetry = step_output.metrics.additional_metrics[
            "workflow_telemetry"
        ]
        self.assertEqual(telemetry["agent_name"], "validator")
        self.assertEqual(
            telemetry["tool_summary"]["tool_names"],
            ("read_official_source",),
        )

    def test_unsupported_claim_can_be_removed(self) -> None:
        original = (
            "O-1A is nonimmigrant [S1]. "
            "The filing fee is always $1 [S1]."
        )
        validator = SequenceValidator(
            response(
                "O-1A is nonimmigrant [S1].",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "official evidence"},
                ),
            )
        )
        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            original,
            citation_check_to_json(
                check_report_citations(original, self.source_package)
            ),
        )

        step_output = build_validator_step_output(
            result,
            original,
            self.source_package,
        )

        self.assertNotIn("$1", step_output.content)

    def test_report_without_citations_uses_exact_neutral_fallback(self) -> None:
        original = "O-1A is an immigrant classification."
        validator = SequenceValidator(
            response(
                "The report did not provide verifiable source citations.",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "official evidence"},
                ),
            )
        )
        citation_json = citation_check_to_json(
            check_report_citations(original, self.source_package)
        )

        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            original,
            citation_json,
        )
        step_output = build_validator_step_output(
            result,
            original,
            self.source_package,
        )

        self.assertEqual(
            step_output.content,
            "The report did not provide verifiable source citations.",
        )
        self.assertEqual(result.attempt_count, 0)
        self.assertEqual(validator.calls, [])

    def test_invalid_source_id_in_corrected_report_is_rejected(self) -> None:
        original = "Invented claim [S99]."
        output_check = check_validator_output(
            original,
            "Still invented [S99].",
            self.source_package,
        )

        self.assertFalse(output_check.passed)
        self.assertEqual(output_check.invalid_source_ids, ("S99",))

    def test_plain_invalid_source_id_token_is_rejected(self) -> None:
        output_check = check_validator_output(
            "Invented claim [S99].",
            "The invalid source S99 was removed.",
            self.source_package,
        )

        self.assertFalse(output_check.passed)
        self.assertEqual(output_check.invalid_source_ids, ("S99",))

    def test_new_allowed_source_id_is_rejected(self) -> None:
        output_check = check_validator_output(
            "Original [S1].",
            "Original [S1]. Added [S2].",
            self.source_package,
        )

        self.assertFalse(output_check.passed)
        self.assertEqual(output_check.new_source_ids, ("S2",))

    def test_existing_source_id_must_keep_bracket_citation_syntax(
        self,
    ) -> None:
        output_check = check_validator_output(
            "First [S1]. Second [S2].",
            "First S1. Second [S2].",
            self.source_package,
        )

        self.assertFalse(output_check.passed)
        self.assertEqual(
            output_check.unbracketed_source_ids,
            ("S1",),
        )

    def test_new_url_is_rejected(self) -> None:
        output_check = check_validator_output(
            "Original [S1].",
            "Original [S1]. https://example.com/new",
            self.source_package,
        )

        self.assertFalse(output_check.passed)
        self.assertEqual(
            output_check.new_urls,
            ("https://example.com/new",),
        )

    def test_step_rejects_new_ids_or_urls(self) -> None:
        validator = SequenceValidator(
            response(
                "Added [S2]. https://example.com/new",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "official evidence"},
                ),
            )
        )
        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            "Original [S1].",
            citation_check_to_json(
                check_report_citations(
                    "Original [S1].",
                    self.source_package,
                )
            ),
        )

        with self.assertRaises(ValidatorOutputPolicyError):
            build_validator_step_output(
                result,
                "Original [S1].",
                self.source_package,
            )

    def test_validator_cannot_drop_every_bracket_citation(self) -> None:
        validator = SequenceValidator(
            response(
                "Corrected factual report without a citation.",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "official evidence"},
                ),
            )
        )
        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            self.original_report,
            self.citation_json,
        )

        with self.assertRaises(ValidatorOutputPolicyError):
            build_validator_step_output(
                result,
                self.original_report,
                self.source_package,
            )

    def test_validator_cannot_cite_source_it_did_not_read(self) -> None:
        validator = SequenceValidator(
            response(
                "Corrected report [S1].",
                tool(
                    "read_official_source",
                    {"source_id": "S2", "content": "different evidence"},
                ),
            )
        )
        result = run_validator_with_tool_control(
            validator,
            self.source_package_json,
            self.original_report,
            self.citation_json,
        )

        with self.assertRaisesRegex(
            CitationEvidenceMismatchError,
            "S1",
        ):
            build_validator_step_output(
                result,
                self.original_report,
                self.source_package,
            )


if __name__ == "__main__":
    unittest.main()
