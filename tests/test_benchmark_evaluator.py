from __future__ import annotations

import unittest

from visa_research.benchmark.evaluator import (
    evaluate_semantic_expectations,
    strip_programmatic_source_appendix,
    validate_expectations_schema,
)


class BenchmarkEvaluatorTests(unittest.TestCase):
    def test_strips_programmatic_source_appendix(self) -> None:
        answer = (
            "Supported answer [S1].\n\n"
            "## Проверенные источники\n\n"
            "- [S1] Official: https://www.uscis.gov/example"
        )

        self.assertEqual(
            strip_programmatic_source_appendix(answer),
            "Supported answer [S1].",
        )

    def test_required_groups_are_and_of_or_patterns(self) -> None:
        expectations = {
            "required_pattern_groups": [
                ["самостоятельн", "self-petition"],
                ["EB-1A"],
            ],
            "forbidden_patterns": [
                "работодатель.{0,20}обязател",
            ],
        }

        passed = evaluate_semantic_expectations(
            "EB-1A допускает самостоятельную подачу.",
            expectations,
        )
        failed = evaluate_semantic_expectations(
            "Для EB-1A работодатель обязателен.",
            expectations,
        )

        self.assertTrue(passed.passed)
        self.assertFalse(failed.passed)
        self.assertIn(
            "missing_required_pattern_group_1",
            failed.failure_reasons,
        )
        self.assertIn(
            "matched_forbidden_pattern_1",
            failed.failure_reasons,
        )

    def test_empty_expectations_are_explicitly_noop(self) -> None:
        result = evaluate_semantic_expectations("", {})

        self.assertTrue(result.passed)
        self.assertEqual(result.failure_reasons, ())
        self.assertEqual(
            validate_expectations_schema(
                {
                    "required_pattern_groups": [],
                    "forbidden_patterns": [],
                },
                field_name="turn.expectations",
            ),
            {},
        )

    def test_rejects_unknown_or_invalid_expectation_schema(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_expectations_schema(
                {"unknown": []},
                field_name="turn.expectations",
            )

        with self.assertRaisesRegex(ValueError, "Invalid benchmark"):
            validate_expectations_schema(
                {
                    "required_pattern_groups": [
                        ["("],
                    ]
                },
                field_name="turn.expectations",
            )

        with self.assertRaisesRegex(ValueError, "matches empty text"):
            validate_expectations_schema(
                {
                    "required_pattern_groups": [
                        [".*"],
                    ]
                },
                field_name="turn.expectations",
            )


if __name__ == "__main__":
    unittest.main()
