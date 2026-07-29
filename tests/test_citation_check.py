import json
import unittest
from types import SimpleNamespace

from visa_workflow import (
    build_citation_step_output,
    build_validator_prompt,
    check_report_citations,
    citation_check_to_json,
    run_validator_with_citation_check,
)


class RecordingValidator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(content="validated report [S1]")


class CitationCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_package = {
            "sources": [
                {"source_id": "S1"},
                {"source_id": "S2"},
                {"source_id": "S10"},
            ],
        }

    def test_valid_citations(self) -> None:
        result = check_report_citations(
            "First fact [S1]. Second fact [S2].",
            self.source_package,
        )

        self.assertEqual(result.citation_count, 2)
        self.assertEqual(result.used_source_ids, ("S1", "S2"))
        self.assertEqual(result.invalid_source_ids, ())
        self.assertEqual(
            result.allowed_source_ids,
            ("S1", "S2", "S10"),
        )
        self.assertTrue(result.has_citations)

    def test_duplicate_citations_count_each_occurrence(self) -> None:
        result = check_report_citations(
            "One [S1], repeated [S1], and another [S2].",
            self.source_package,
        )

        self.assertEqual(result.citation_count, 3)
        self.assertEqual(result.used_source_ids, ("S1", "S2"))
        self.assertEqual(result.invalid_source_ids, ())

    def test_unknown_source_id_is_invalid(self) -> None:
        result = check_report_citations(
            "Supported [S1], invented [S99].",
            self.source_package,
        )

        self.assertEqual(result.citation_count, 2)
        self.assertEqual(result.used_source_ids, ("S1", "S99"))
        self.assertEqual(result.invalid_source_ids, ("S99",))

    def test_report_without_citations(self) -> None:
        result = check_report_citations(
            "A report with no source references.",
            self.source_package,
        )

        self.assertEqual(result.citation_count, 0)
        self.assertEqual(result.used_source_ids, ())
        self.assertEqual(result.invalid_source_ids, ())
        self.assertFalse(result.has_citations)

    def test_mixed_case_and_malformed_citations_are_not_accepted(self) -> None:
        result = check_report_citations(
            "Wrong [s1], [S 1], [S-1]; exact [S2].",
            self.source_package,
        )

        self.assertEqual(result.citation_count, 1)
        self.assertEqual(result.used_source_ids, ("S2",))
        self.assertEqual(result.invalid_source_ids, ())

    def test_result_serializes_as_workflow_json(self) -> None:
        result = check_report_citations(
            "Fact [S10] and invention [S99].",
            self.source_package,
        )

        payload = json.loads(citation_check_to_json(result))

        self.assertEqual(payload["citation_count"], 2)
        self.assertEqual(payload["used_source_ids"], ["S10", "S99"])
        self.assertEqual(payload["invalid_source_ids"], ["S99"])
        self.assertTrue(payload["has_citations"])

    def test_workflow_step_output_preserves_citation_metrics(self) -> None:
        step_output = build_citation_step_output(
            "Supported [S1], invented [S99].",
            self.source_package,
        )
        payload = json.loads(step_output.content)

        self.assertTrue(step_output.success)
        self.assertEqual(
            step_output.step_name,
            "check_research_citations",
        )
        self.assertEqual(payload["citation_count"], 2)
        self.assertEqual(payload["invalid_source_ids"], ["S99"])
        telemetry = step_output.metrics.additional_metrics[
            "workflow_telemetry"
        ]
        self.assertEqual(
            telemetry["citation_check"]["invalid_source_ids"],
            ("S99",),
        )

    def test_validator_prompt_contains_programmatic_result(self) -> None:
        package_with_secret_content = {
            "sources": [
                {
                    "source_id": "S1",
                    "content": "SECRET VALIDATOR EVIDENCE",
                },
            ],
        }
        citation_json = citation_check_to_json(
            check_report_citations(
                "Invented claim [S99].",
                self.source_package,
            )
        )
        prompt = build_validator_prompt(
            json.dumps(package_with_secret_content),
            "Invented claim [S99].",
            citation_json,
        )

        self.assertIn("PROGRAMMATIC CITATION CHECK", prompt)
        self.assertIn('"invalid_source_ids": [', prompt)
        self.assertIn('"S99"', prompt)
        self.assertIn("Do not invent replacement source IDs.", prompt)
        self.assertNotIn("SECRET VALIDATOR EVIDENCE", prompt)

    def test_programmatic_result_is_passed_to_validator_run(self) -> None:
        validator = RecordingValidator()
        citation_json = citation_check_to_json(
            check_report_citations(
                "Verified claim [S1].",
                self.source_package,
            )
        )

        response = run_validator_with_citation_check(
            validator,
            json.dumps(self.source_package),
            "Verified claim [S1].",
            citation_json,
        )

        self.assertEqual(response.content, "validated report [S1]")
        self.assertEqual(len(validator.calls), 1)
        prompt, kwargs = validator.calls[0]
        self.assertIn('"citation_count": 1', prompt)
        self.assertIn('"invalid_source_ids": []', prompt)
        self.assertFalse(kwargs["stream"])
        self.assertEqual(
            kwargs["dependencies"]["source_package"],
            self.source_package,
        )


if __name__ == "__main__":
    unittest.main()
